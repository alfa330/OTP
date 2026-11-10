# -*- coding: utf-8 -*-
"""«Воронка найма» (#404) на настоящем Postgres: HIRING_TEST_PORT — порт кластера.

Чистые правила проверяет tests/test_hiring.py. Здесь — процесс на схеме раздела:

  * приём из всех источников через одну точку: дубли по телефону (ТЗ 5.3) — повторный отклик,
    телефон сотрудника, повторное обращение после завершённой карточки;
  * «Взять в работу» закрепляет за одним рекрутером, второй получает «Уже в работе» (ТЗ 7.1);
  * касания ставят подстатус сами, три недозвона подряд — подсказка (ТЗ 4.2, 5.4);
  * переходы по правилам ТЗ 4.2 со всеми следствиями: группа, обучение, линия, смены,
    подтверждение «Отработал 7 дней» руководителем отдела, мотивация (ТЗ 7.2);
  * отказ и отсев с причинами, повторная активация руководителем;
  * возврат в пул без касаний месяц, напоминания о дате контакта, уведомления колокола;
  * загрузка базы файлом, вебхук площадок, отклики OLX из журнала робота;
  * дашборд по когорте, список за цифрой, выгрузка.

Только 127.0.0.1, своя схема со случайным именем, в конце она удаляется. Люди выдуманы.

    HIRING_TEST_PORT=55432 python -m pytest tests/test_hiring_postgres.py
"""

import contextlib
import os
import uuid
from datetime import datetime, timedelta

import pytest

from hiring import notices, queries, reports, rules, schema, wa_media
from hiring.service import HiringError, HiringService

PORT = os.environ.get('HIRING_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (HIRING_TEST_PORT)')

SUPER, HR_HEAD, LEAD_GRANT, REC1, REC2, REMOTE, TRAINER, OP_HEAD, STRANGER, EMPLOYEE = 1, 2, 3, 10, 11, 12, 20, 30, 40, 50
NOW = datetime(2026, 10, 15, 12, 0)

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text UNIQUE, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer, phone text, telegram_id bigint);
    -- Часы дня по статусам работы («Учет часов», daily_hours) — по ним считаются смены на линии.
    CREATE TABLE daily_hours (id serial PRIMARY KEY, operator_id integer NOT NULL, day date NOT NULL,
                              work_time double precision NOT NULL DEFAULT 0, auto_aggregated boolean NOT NULL DEFAULT TRUE,
                              UNIQUE (operator_id, day));
    -- Членство в линии Binotel «Удаленного КЦ» (dial_list_line_members) — по нему звонок находит рекрутера.
    CREATE TABLE dial_list_line_members (id bigserial PRIMARY KEY, user_id integer NOT NULL, department_id integer NOT NULL,
                                         internal_number varchar(64) NOT NULL, assigned_at timestamptz NOT NULL DEFAULT now(),
                                         released_at timestamptz);
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        (1499, 'hr', 'HR', 2), (367, 'op', 'Отдел продаж', 30), (1, 'szov', 'СЗоВ', NULL);
    INSERT INTO users (id, name, role, status, department_id, phone, telegram_id) VALUES
        (1, 'Супер Админов', 'super_admin', 'working', 1, NULL, NULL),
        (2, 'Глава HR', 'admin', 'working', 1499, NULL, NULL),
        (3, 'Выданный Руководитель', 'operator', 'working', 1, NULL, NULL),
        (10, 'Первый Рекрутер', 'operator', 'working', 1, NULL, 1010),
        (11, 'Второй Рекрутер', 'operator', 'working', 1, NULL, NULL),
        (12, 'Удалённый Рекрутер', 'operator', 'working', 1, NULL, NULL),
        (20, 'Тренер Групп', 'trainer', 'working', 367, NULL, NULL),
        (30, 'Глава Продаж', 'admin', 'working', 367, NULL, 3030),
        (40, 'Чужой Оператор', 'operator', 'working', 1, NULL, NULL),
        (50, 'Действующий Сотрудник', 'operator', 'working', 367, '+7 (777) 999 88 77', NULL),
        (51, 'Бывший Сотрудник', 'operator', 'fired', 367, '8 777 111 22 33', NULL);
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user=os.environ.get('HIRING_TEST_USER', 'postgres'),
                                dbname='postgres', connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
            cur.execute("SET statement_timeout TO '20s'")
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
    name = 't_hiring_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            schema.init_hiring_schema(cur)
            schema.init_hiring_schema(cur)   # рестарт прода: идемпотентно, без второго набора справочников
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)
        admin.commit()
        admin.close()


class Clock:
    def __init__(self, moment):
        self.now = moment

    def __call__(self):
        return self.now


@pytest.fixture
def world(db):
    clock = Clock(NOW)
    sent = []
    service = HiringService(cursor_factory=db._get_cursor, now=clock,
                            send_telegram=lambda chat_id, text, markup: sent.append((chat_id, text, markup)) or _Ok(),
                            web_app_base_url='https://portal.test')
    with db._get_cursor() as cur:
        queries.upsert_person(cur, LEAD_GRANT, 'lead', SUPER, NOW)
        queries.upsert_person(cur, REC1, 'staff', SUPER, NOW)
        queries.upsert_person(cur, REC2, 'staff', SUPER, NOW)
        queries.upsert_person(cur, REMOTE, 'remote', SUPER, NOW)
        vacancy_id = queries.create_vacancy(cur, 'Оператор продаж', 367, True, SUPER, NOW)
        group_id = queries.create_group(cur, 'Группа 1', NOW.date() + timedelta(days=5), TRAINER, 367, vacancy_id, SUPER, NOW)
        sources = {row['code']: row for row in queries.list_sources(cur)}
    return {'db': db, 'service': service, 'clock': clock, 'sent': sent, 'vacancy_id': vacancy_id, 'group_id': group_id,
            'sources': sources}


class _Ok:
    status_code = 200


def ctx_of(world, user_id):
    return world['service'].context(user_id)


def add_response(world, user_id, phone, **extra):
    payload = {'phone': phone, 'source_id': world['sources']['manual']['id'], 'full_name': extra.pop('full_name', 'Кандидат'),
               'vacancy_id': world['vacancy_id']}
    payload.update(extra)
    return world['service'].add_response(ctx_of(world, user_id), payload)


def take_and_touch(world, user_id, phone, **extra):
    outcome, card = add_response(world, user_id, phone, **extra)
    assert outcome == 'created'
    service = world['service']
    card = service.take(ctx_of(world, user_id), card['id'])
    service.touch(ctx_of(world, user_id), card['id'], {'kind': 'call', 'result': 'dial', 'body': 'договорились'})
    return card


# ─── Схема и справочники ───────────────────────────────────────────────────────

def test_seed_and_context(world):
    with world['db']._get_cursor() as cur:
        reasons = queries.list_reasons(cur)
        assert len(reasons) == 30
        assert {r['kind'] for r in reasons} == set(rules.REASON_KINDS)
        sources = queries.list_sources(cur)
        codes = [s['code'] for s in sources]
        assert codes[:4] == ['hh', 'kaspi', 'olx', 'target']
        # Номер WhatsApp найма появился после выкладки: долит в заполненный справочник один раз,
        # последним из откликов (схема разворачивается дважды — рестарт прода).
        assert codes.count('whatsapp') == 1
        assert [s['code'] for s in sources if s['kind'] == 'responses'][-1] == 'whatsapp'
        assert {row['code'] for row in queries.list_integrations(cur)} == {'hh', 'olx', 'webhook', 'whatsapp'}
    ctx = ctx_of(world, HR_HEAD)
    assert ctx['headed_codes'] == ['hr'] and ctx['grant'] is None
    assert ctx_of(world, TRAINER)['trainer_group_ids'] == [world['group_id']]
    assert ctx_of(world, OP_HEAD)['vacancy_department_ids'] == [367]
    assert ctx_of(world, 999) is None


# ─── Приём и дубли (ТЗ 5.3) ────────────────────────────────────────────────────

def test_intake_dedupe_rules(world):
    service = world['service']
    outcome, card = add_response(world, REC1, '8 705 555 22 33', full_name='Иванов')
    assert outcome == 'created' and card['stage'] == 'responses' and card['phone'] == '+77055552233'
    outcome, same = add_response(world, REC2, '+7 705 555-22-33')
    assert outcome == 'repeat' and same['id'] == card['id']
    with world['db']._get_cursor() as cur:
        touches = queries.card_touches(cur, card['id'])
        assert [t['kind'] for t in touches] == ['repeat_response']
        assert queries.list_cards(cur, {'lead': True}, SUPER, {})[0] == 1
    with pytest.raises(HiringError) as caught:
        add_response(world, REC1, '7 777 999 88 77')
    assert caught.value.code == 'HIRING_EMPLOYEE_PHONE'
    outcome, former = add_response(world, REC1, '8 777 111 22 33')
    assert outcome == 'created'
    with world['db']._get_cursor() as cur:
        assert queries.card_touches(cur, former['id'])[0]['result'] == 'former_employee'
        assert queries.bell(cur, REC2, 10)[0] == 2   # два новых отклика в пуле — обоим рекрутерам
    with pytest.raises(HiringError) as caught:
        add_response(world, REC1, '123')
    assert caught.value.code == 'HIRING_BAD_PHONE'
    with pytest.raises(HiringError) as caught:
        service.add_response(ctx_of(world, REC1), {'phone': '8 705 000 00 01'})
    assert caught.value.code == 'HIRING_NEED_SOURCE'
    with pytest.raises(HiringError) as caught:
        add_response(world, STRANGER, '8 705 000 00 02')
    assert caught.value.code == 'HIRING_FORBIDDEN'


# ─── Закрепление (ТЗ 7.1) ──────────────────────────────────────────────────────

def test_take_is_exclusive_and_visibility(world):
    service = world['service']
    _outcome, card = add_response(world, REC1, '8 705 222 33 44')
    taken = service.take(ctx_of(world, REC1), card['id'])
    assert taken['stage'] == 'in_work' and taken['recruiter_id'] == REC1 and taken['candidate_no'] == 1
    with pytest.raises(HiringError) as caught:
        service.take(ctx_of(world, REC2), card['id'])
    assert caught.value.code == 'HIRING_ALREADY_TAKEN' and caught.value.status == 409
    assert service.visible(ctx_of(world, REC1), taken)
    assert not service.visible(ctx_of(world, REC2), taken)
    assert service.visible(ctx_of(world, LEAD_GRANT), taken)
    assert service.visible(ctx_of(world, OP_HEAD), taken)          # отдел вакансии — его
    assert not service.visible(ctx_of(world, TRAINER), taken)      # группы ещё нет
    with pytest.raises(HiringError) as caught:
        service.touch(ctx_of(world, REC2), card['id'], {'kind': 'comment', 'body': 'чужое'})
    assert caught.value.code == 'HIRING_NOT_FOUND'
    # «В работе» через move — по этапу карточки: из пула это то же атомарное взятие, флаг из тела не обходит его.
    _outcome, other = add_response(world, REC1, '8 705 222 33 45')
    via_move = service.move(ctx_of(world, REC2), other['id'], 'in_work', {'reactivate': True, 'recruiter_id': REC1})
    assert via_move['recruiter_id'] == REC2 and via_move['candidate_no'] and via_move['taken_at']
    with pytest.raises(HiringError) as caught:
        service.move(ctx_of(world, REC1), other['id'], 'in_work', {'reactivate': True})
    # Чужая закреплённая карточка для другого рекрутера не существует вовсе — ни взять, ни перевести.
    assert caught.value.code == 'HIRING_NOT_FOUND'
    # Удалённый рекрутер ищет по цифрам телефона только среди своих карточек — иначе поиск по карточке
    # пула (телефон скрыт) выдавал бы «нашлось / не нашлось» о скрытом номере.
    add_response(world, REC1, '8 705 222 33 46')
    with world['db']._get_cursor() as cur:
        caps_remote = {'recruiter': True, 'remote': True}
        total, _rows = queries.list_cards(cur, caps_remote, REMOTE, {'query': '2223346', 'phone_scope_user_id': REMOTE})
        assert total == 0
        total, _rows = queries.list_cards(cur, caps_remote, REMOTE, {'query': '2223346'})
        assert total == 1   # без границы (штатный рекрутер) карточка в пуле нашлась бы
    # Снятая выдача снимает и права владельца на свои карточки, даже если человек остался в разделе тренером.
    with world['db']._get_cursor() as cur:
        queries.create_group(cur, 'Группа Б', NOW.date(), REC1, 367, world['vacancy_id'], SUPER, NOW)
        queries.deactivate_person(cur, REC1, NOW)
    former = ctx_of(world, REC1)
    assert former['grant'] is None and former['trainer_group_ids']
    assert not service.visible(former, taken)
    with pytest.raises(HiringError) as caught:
        service.touch(former, taken['id'], {'kind': 'call', 'result': 'dial'})
    assert caught.value.code == 'HIRING_NOT_FOUND'
    with world['db']._get_cursor() as cur:
        # Доска второго рекрутера: его карточка (взятая через move) и пул; чужая закреплённая — нет.
        columns, counts = queries.board(cur, {'recruiter': True}, REC2, {})
        assert counts == {'in_work': 1, 'responses': 1} and columns['in_work'][0]['id'] == other['id']
        columns, counts = queries.board(cur, {'recruiter': True}, REC1, {})
        assert counts == {'in_work': 1, 'responses': 1} and columns['in_work'][0]['id'] == card['id']
        assert queries.card_assignments(cur, card['id'])[0]['reason'] == 'take'


def test_remote_recruiter_phone_rules(world):
    from hiring import access
    _outcome, card = add_response(world, REC1, '8 705 333 44 55')
    remote = ctx_of(world, REMOTE)
    assert not access.sees_phone(remote, card)
    taken = world['service'].take(remote, card['id'])
    assert access.sees_phone(remote, taken)


# ─── Касания и подстатусы (ТЗ 4.2, 5.4) ────────────────────────────────────────

def test_touches_set_substage_and_suggest_refusal(world):
    service = world['service']
    _outcome, card = add_response(world, REC1, '8 705 444 55 66')
    me = ctx_of(world, REC1)
    with pytest.raises(HiringError) as caught:
        service.touch(me, card['id'], {'kind': 'call', 'result': 'dial'})
    assert caught.value.code == 'HIRING_NOT_TAKEN'
    service.take(me, card['id'])
    for _ in range(3):
        card = service.touch(me, card['id'], {'kind': 'call', 'result': 'no_answer'})
    assert card['substage'] == 'no_answer' and card['no_answer_streak'] == 3
    # Наше сообщение — ещё не ответ (владелец 09.11.2026: «Недозвон / не ответил» — не ответил ни на звонок, ни на
    # WhatsApp): «Написать в WhatsApp» статус не снимает, снимает только ответ кандидата.
    card = service.touch(me, card['id'], {'kind': 'whatsapp'})
    assert card['substage'] == 'no_answer' and card['no_answer_streak'] == 3
    card = service.set_substage(me, card['id'], 'dial')
    card = service.touch(me, card['id'], {'kind': 'whatsapp'})
    assert card['substage'] == 'whatsapp' and card['no_answer_streak'] == 3
    card = service.touch(me, card['id'], {'kind': 'call', 'result': 'dial', 'next_contact_at': '2026-10-16T10:00'})
    assert card['substage'] == 'dial' and card['no_answer_streak'] == 0
    assert card['next_contact_at'] == datetime(2026, 10, 16, 10, 0)
    # Звонок без исхода — ждёт телефонию (владелец 30.10.2026): подстатус и серия не меняются.
    card = service.touch(me, card['id'], {'kind': 'call'})
    assert card['substage'] == 'dial' and card['no_answer_streak'] == 0
    with pytest.raises(HiringError):
        service.touch(me, card['id'], {'kind': 'call', 'result': 'maybe'})
    with pytest.raises(HiringError):
        service.touch(me, card['id'], {'kind': 'comment', 'body': '  '})
    card = service.set_substage(me, card['id'], 'no_answer')
    assert card['substage'] == 'no_answer'
    with pytest.raises(HiringError):
        service.set_substage(me, card['id'], 'came')


# ─── Переходы (ТЗ 4.2) и мотивация (ТЗ 7.2) ────────────────────────────────────

def test_full_path_to_hired(world):
    service, clock = world['service'], world['clock']
    me, trainer, head, lead = ctx_of(world, REC1), ctx_of(world, TRAINER), ctx_of(world, OP_HEAD), ctx_of(world, LEAD_GRANT)
    _outcome, card = add_response(world, REC1, '8 705 555 66 77', full_name='Асель')
    card = service.take(me, card['id'])
    with pytest.raises(HiringError) as caught:
        service.move(me, card['id'], 'invited_office', {'office_visit_at': '2026-10-16T10:00'})
    assert caught.value.code == 'HIRING_NO_TOUCH'
    service.touch(me, card['id'], {'kind': 'call', 'result': 'dial'})
    with pytest.raises(HiringError) as caught:
        service.move(me, card['id'], 'invited_office', {})
    assert caught.value.code == 'HIRING_NEED_OFFICE_DATE'
    card = service.move(me, card['id'], 'invited_office', {'office_visit_at': '2026-10-16T10:00'})
    assert card['stage'] == 'invited_office' and card['substage'] == 'expected'
    with pytest.raises(HiringError) as caught:
        service.move(me, card['id'], 'invited_training', {'group_id': world['group_id']})
    assert caught.value.code == 'HIRING_NOT_CAME'
    card = service.set_substage(me, card['id'], 'came')
    card = service.move(me, card['id'], 'invited_training', {'group_id': world['group_id']})
    assert card['stage'] == 'invited_training' and card['group_id'] == world['group_id']
    assert service.visible(trainer, card)
    with pytest.raises(HiringError) as caught:
        service.move(ctx_of(world, REC2), card['id'], 'on_training', {})
    assert caught.value.code == 'HIRING_NOT_FOUND'
    card = service.move(trainer, card['id'], 'on_training', {})
    assert card['stage'] == 'on_training' and card['training_started_at'] == NOW.date() + timedelta(days=5)
    card = service.move(trainer, card['id'], 'on_line', {'line_started_at': '2026-10-25'})
    assert card['stage'] == 'on_line' and card['line_started_at'] == datetime(2026, 10, 25).date()
    with world['db']._get_cursor() as cur:
        assert queries.get_motivation(cur, card['id'])['status'] == 'pending'
        total, rows = queries.bell(cur, OP_HEAD, 10)
        assert total == 1 and rows[0]['kind'] == notices.KIND_ON_LINE
    # Смены — из статусов работы сотрудника (владелец 30.10.2026), руками не ведутся: пока учётки с
    # телефоном кандидата в «Учете сотрудников» нет — смен нет.
    assert service.sync_shifts() == {'cards': 1, 'matched': 0, 'shifts': 0}
    with world['db']._get_cursor() as cur:
        cur.execute("INSERT INTO users (id, name, role, status, department_id, phone) VALUES (77, 'Новый Оператор', 'operator', 'working', 367, %s)",
                    ('8 (%s) %s-%s-%s' % (card['phone'][2:5], card['phone'][5:8], card['phone'][8:10], card['phone'][10:]),))
        for day in range(25, 31):
            cur.execute("INSERT INTO daily_hours (operator_id, day, work_time) VALUES (77, %s, 7.5)", (datetime(2026, 10, day).date(),))
        cur.execute("INSERT INTO daily_hours (operator_id, day, work_time) VALUES (77, '2026-10-31', 0.75)")   # меньше часа — не смена
        cur.execute("INSERT INTO daily_hours (operator_id, day, work_time) VALUES (77, '2026-10-20', 8)")      # до выхода на линию
    clock.now = datetime(2026, 11, 3, 12, 0)
    assert service.sync_shifts() == {'cards': 1, 'matched': 1, 'shifts': 6}
    with world['db']._get_cursor() as cur:
        fresh = queries.get_card(cur, card['id'])
        assert fresh['shifts_done'] == 6 and fresh['employee_user_id'] == 77 and fresh['employee_name'] == 'Новый Оператор'
        shifts = queries.card_shifts(cur, card['id'])
        assert [str(s['shift_date']) for s in shifts] == ['2026-10-%02d' % d for d in range(25, 31)]
        assert shifts[0]['source'] == 'auto' and float(shifts[0]['work_hours']) == 7.5
    with pytest.raises(HiringError) as caught:
        service.move(head, card['id'], 'hired', {})
    assert caught.value.code == 'HIRING_NEED_SHIFTS'
    # Седьмой день с часом работы — седьмая смена и колокол руководителю отдела; повторный прогон ничего не удваивает.
    with world['db']._get_cursor() as cur:
        cur.execute("INSERT INTO daily_hours (operator_id, day, work_time) VALUES (77, '2026-11-01', 1.0)")
    card = service.sync_card_shifts(head, card['id'])
    assert card['shifts_done'] == 7
    assert service.sync_shifts() == {'cards': 1, 'matched': 1, 'shifts': 7}
    with world['db']._get_cursor() as cur:
        total, rows = queries.bell(cur, OP_HEAD, 10)
        assert any(row['title'].startswith('Семь смен на линии') for row in rows)
    with pytest.raises(HiringError) as caught:
        service.sync_card_shifts(ctx_of(world, REC2), card['id'])
    assert caught.value.code == 'HIRING_NOT_FOUND'
    with pytest.raises(HiringError) as caught:
        service.move(lead, card['id'], 'hired', {})
    assert caught.value.code == 'HIRING_FORBIDDEN'
    card = service.move(head, card['id'], 'hired', {})
    assert card['stage'] == 'hired' and card['is_closed'] and card['hired_by'] == OP_HEAD
    with world['db']._get_cursor() as cur:
        motivation = queries.get_motivation(cur, card['id'])
        assert motivation['status'] == 'counted' and motivation['recruiter_id'] == REC1
        assert queries.bell(cur, REC1, 10)[1][0]['kind'] == notices.KIND_MOTIVATION
        stages = [row['stage'] for row in queries.card_stages(cur, card['id'])]
        assert stages == ['responses', 'in_work', 'invited_office', 'invited_training', 'on_training', 'on_line', 'hired']
        facts = queries.plan_facts(cur, datetime(2026, 11, 1), datetime(2026, 12, 1))
        assert facts == [{'vacancy_id': world['vacancy_id'], 'department_id': 367, 'recruiter_id': REC1}]
    paid = service.set_motivation(lead, card['id'], 'paid')
    assert paid['status'] == 'paid'
    with pytest.raises(HiringError):
        service.set_motivation(me, card['id'], 'paid')


def test_refusal_and_reactivation(world):
    service = world['service']
    me, lead = ctx_of(world, REC1), ctx_of(world, LEAD_GRANT)
    card = take_and_touch(world, REC1, '8 705 666 77 88')
    with world['db']._get_cursor() as cur:
        other = queries.reason_by_name(cur, 'refusal_candidate', 'Другое')
        no_contact = queries.reason_by_name(cur, 'refusal_candidate', rules.REASON_NO_CONTACT)
        ours = queries.reason_by_name(cur, 'refusal_ours', 'Языковой барьер')
    with pytest.raises(HiringError) as caught:
        service.move(me, card['id'], 'refused', {'initiator': 'candidate', 'reason_id': ours['id']})
    assert caught.value.code == 'HIRING_BAD_REASON'
    with pytest.raises(HiringError) as caught:
        service.move(me, card['id'], 'refused', {'initiator': 'candidate', 'reason_id': other['id']})
    assert caught.value.code == 'HIRING_NEED_COMMENT'
    card = service.move(me, card['id'], 'refused', {'initiator': 'candidate', 'reason_id': no_contact['id']})
    assert card['is_closed'] and card['substage'] == 'candidate' and card['close_reason_text'] == rules.REASON_NO_CONTACT
    assert card['close_initiator'] == 'candidate'
    # Закрытая карточка: касание только комментарием, звонок — нет.
    with pytest.raises(HiringError) as caught:
        service.touch(me, card['id'], {'kind': 'call', 'result': 'dial'})
    assert caught.value.code == 'HIRING_CLOSED'
    service.touch(me, card['id'], {'kind': 'comment', 'body': 'позже'})
    # Тот же телефон снова — новая карточка с пометкой «повторное обращение».
    outcome, fresh = add_response(world, REC1, '8 705 666 77 88')
    assert outcome == 'created' and fresh['repeat_of_card_id'] == card['id']
    # Повторная активация старой невозможна, пока открыта новая, — ключ дубля.
    with pytest.raises(HiringError) as caught:
        service.move(lead, card['id'], 'in_work', {'reactivate': True, 'recruiter_id': REC2})
    assert caught.value.code == 'HIRING_PHONE_OPEN'
    with pytest.raises(HiringError) as caught:
        service.move(me, card['id'], 'in_work', {'reactivate': True})
    assert caught.value.code == 'HIRING_FORBIDDEN'
    # Закрыли новую — старую можно вернуть в работу за другим рекрутером (но только за рекрутером).
    service.take(me, fresh['id'])
    service.touch(me, fresh['id'], {'kind': 'comment', 'body': 'x'})
    service.move(me, fresh['id'], 'refused', {'initiator': 'ours', 'reason_id': ours['id']})
    with pytest.raises(HiringError) as caught:
        service.move(lead, card['id'], 'in_work', {'reactivate': True, 'recruiter_id': TRAINER})
    assert caught.value.code == 'HIRING_BAD_RECRUITER'
    card = service.move(lead, card['id'], 'in_work', {'reactivate': True, 'recruiter_id': REC2})
    assert card['stage'] == 'in_work' and not card['is_closed'] and card['recruiter_id'] == REC2 and card['reactivations'] == 1
    assert card['close_reason_text'] is None
    with world['db']._get_cursor() as cur:
        assert queries.card_assignments(cur, card['id'])[-1]['reason'] == 'reactivate'


def test_drop_on_line_cancels_motivation(world):
    service = world['service']
    me, trainer, head = ctx_of(world, REC1), ctx_of(world, TRAINER), ctx_of(world, OP_HEAD)
    card = take_and_touch(world, REC1, '8 705 777 88 99')
    card = service.move(me, card['id'], 'invited_training', {'group_id': world['group_id']})
    card = service.move(trainer, card['id'], 'on_training', {'training_started_at': '2026-10-20'})
    with world['db']._get_cursor() as cur:
        drop_we = queries.reason_by_name(cur, 'drop_we', 'Пропуски обучения')
        drop_self = queries.reason_by_name(cur, 'drop_self', 'Нашел другую работу')
    with pytest.raises(HiringError) as caught:
        service.move(trainer, card['id'], 'dropped', {'initiator': 'we', 'reason_id': drop_we['id']})
    assert caught.value.code == 'HIRING_NEED_DAY'
    card = service.move(me, card['id'], 'on_line', {'line_started_at': '2026-10-25'})
    # Переназначение на линии переносит ожидающее начисление к новому рекрутеру (ТЗ 7.2).
    card = service.reassign(ctx_of(world, LEAD_GRANT), card['id'], REC2, 'передача на линии')
    with world['db']._get_cursor() as cur:
        pending = queries.get_motivation(cur, card['id'])
        assert pending['status'] == 'pending' and pending['recruiter_id'] == REC2
    with pytest.raises(HiringError) as caught:
        service.move(trainer, card['id'], 'dropped', {'initiator': 'self', 'reason_id': drop_self['id'], 'day': 2})
    assert caught.value.code == 'HIRING_FORBIDDEN'
    card = service.move(head, card['id'], 'dropped', {'initiator': 'self', 'reason_id': drop_self['id'], 'day': 2})
    assert card['is_closed'] and card['close_stage'] == 'line' and card['close_day'] == 2
    with world['db']._get_cursor() as cur:
        assert queries.get_motivation(cur, card['id'])['status'] == 'cancelled'


# ─── Руководитель: переназначение, отключение ──────────────────────────────────

def test_reassign_release_and_deactivate(world):
    service = world['service']
    lead = ctx_of(world, LEAD_GRANT)
    card = take_and_touch(world, REC1, '8 705 123 00 01')
    with pytest.raises(HiringError) as caught:
        service.reassign(ctx_of(world, REC1), card['id'], REC2, 'хочу')
    assert caught.value.code == 'HIRING_FORBIDDEN'
    with pytest.raises(HiringError) as caught:
        service.reassign(lead, card['id'], REC2, '')
    assert caught.value.code == 'HIRING_NEED_COMMENT'
    card = service.reassign(lead, card['id'], REC2, 'перераспределение')
    assert card['recruiter_id'] == REC2
    with world['db']._get_cursor() as cur:
        assert queries.bell(cur, REC1, 10)[1][0]['kind'] == notices.KIND_REASSIGNED
        assert queries.bell(cur, REC2, 10)[1][0]['title'].startswith('Вам передана')
    card = service.reassign(lead, card['id'], None, 'в пул')
    assert card['stage'] == 'responses' and card['recruiter_id'] is None
    with pytest.raises(HiringError) as caught:
        service.reassign(lead, card['id'], STRANGER, 'не рекрутер')
    assert caught.value.code == 'HIRING_BAD_RECRUITER'
    other = take_and_touch(world, REC2, '8 705 123 00 02')
    returned = service.deactivate_person(lead, REC2)
    assert returned == 1
    with world['db']._get_cursor() as cur:
        assert queries.get_card(cur, other['id'])['stage'] == 'responses'
        assert queries.card_assignments(cur, other['id'])[-1]['reason'] == 'recruiter_off'
    assert not service.context(REC2)['grant']


# ─── Планировщик (ТЗ 7.1, 8.3) ────────────────────────────────────────────────

def test_auto_return_and_next_contact_notifications(world):
    service, clock, sent = world['service'], world['clock'], world['sent']
    me = ctx_of(world, REC1)
    idle = take_and_touch(world, REC1, '8 705 321 00 01')
    fresh = take_and_touch(world, REC1, '8 705 321 00 02')
    clock.now = NOW + timedelta(days=20)
    service.touch(me, fresh['id'], {'kind': 'comment', 'body': 'живая', 'next_contact_at': (NOW + timedelta(days=21)).isoformat()})
    clock.now = NOW + timedelta(days=31)
    assert service.auto_return() == 1
    with world['db']._get_cursor() as cur:
        card = queries.get_card(cur, idle['id'])
        assert card['stage'] == 'responses' and card['recruiter_id'] is None
        assert queries.card_assignments(cur, idle['id'])[-1]['reason'] == 'auto_timeout'
        kinds = {row['kind'] for row in queries.bell(cur, REC1, 20)[1]}
        assert notices.KIND_RETURNED in kinds
        assert notices.KIND_RETURNED in {row['kind'] for row in queries.bell(cur, HR_HEAD, 20)[1]}
        assert queries.get_card(cur, fresh['id'])['stage'] == 'in_work'
    assert len(sent) == 1 and sent[0][0] == 1010 and 'вернулась в пул' in sent[0][1]
    assert sent[0][2]['inline_keyboard'][0][0]['url'] == 'https://portal.test/?view=hiring&card=%s' % idle['id']
    # Дата следующего контакта наступила — одно уведомление, повторный прогон молчит.
    assert service.notify_next_contacts() == 1
    assert service.notify_next_contacts() == 0
    with world['db']._get_cursor() as cur:
        rows = [row for row in queries.bell(cur, REC1, 20)[1] if row['kind'] == notices.KIND_NEXT_CONTACT]
        assert len(rows) == 1 and rows[0]['tone'] == 'warning'
        # Открыл карточку — уведомления по ней погашены.
        queries.mark_card_seen(cur, REC1, fresh['id'], clock.now)
        assert not [row for row in queries.bell(cur, REC1, 20)[1] if row['card_id'] == fresh['id']]
    clock.now = NOW + timedelta(days=40)
    assert service.overdue_digest() == 1
    assert 'просроченные задачи' in sent[-1][1]


# ─── Источники: файл, вебхук, OLX (ТЗ 5) ──────────────────────────────────────

def test_upload_contacts(world):
    service = world['service']
    lead = ctx_of(world, LEAD_GRANT)
    add_response(world, REC1, '8 705 000 00 01')      # открытая карточка — строка станет повторным откликом
    raw = ('ФИО;Телефон;Город\nА;8 705 000 00 01;Алматы\nБ;8 705 000 00 02;Астана\nВ;8 705 000 00 02;Астана\n'
           'Г;+7 777 999 88 77;Алматы\nД;нет;\n').encode('utf-8')
    with pytest.raises(HiringError) as caught:
        service.upload_contacts(ctx_of(world, REC1), raw, 'база.csv', world['sources']['base_bought']['id'])
    assert caught.value.code == 'HIRING_FORBIDDEN'
    with pytest.raises(HiringError) as caught:
        service.upload_contacts(lead, raw, 'база.csv', world['sources']['manual']['id'])
    assert caught.value.code == 'HIRING_NEED_SOURCE'
    batch = service.upload_contacts(lead, raw, 'база.csv', world['sources']['base_bought']['id'])
    assert (batch['total_rows'], batch['loaded'], batch['duplicates'], batch['errors']) == (5, 1, 1, 3)
    assert [row['error'] for row in batch['error_rows']] == ['Повтор номера в файле', 'Телефон действующего сотрудника', 'Пустой или неразборный номер']
    with world['db']._get_cursor() as cur:
        total, rows = queries.list_cards(cur, {'lead': True}, SUPER, {'stage': 'contacts'})
        assert total == 1 and rows[0]['phone'] == '+77050000002' and rows[0]['batch_id'] == batch['id']
        assert rows[0]['source_name'] == 'Купленная база'


def test_external_intake(world):
    service = world['service']
    results = service.external_intake([
        {'phone': '8 705 800 00 01', 'name': 'Таргет Один', 'city': 'Алматы', 'campaign': 'осень', 'ref': 'https://fb.com/lead/1'},
        {'phone': '8 705 800 00 01'},
        {'phone': 'мусор'},
        {'phone': '+7 777 999 88 77'},
    ], source_code='target', actor_label='Партнёр: тест')
    assert [row['status'] for row in results] == ['created', 'repeat', 'invalid', 'employee']
    with world['db']._get_cursor() as cur:
        card = queries.get_card(cur, results[0]['card_id'])
        assert card['source_code'] == 'target' and card['campaign'] == 'осень' and card['full_name'] == 'Таргет Один'
        assert queries.get_integration(cur, 'webhook')['received_total'] == 2
    with pytest.raises(HiringError):
        service.external_intake([{'phone': '8 705 800 00 09'}], source_code='nope', actor_label='x')


def test_olx_feed(world):
    service = world['service']
    with world['db']._get_cursor() as cur:
        cur.execute("""
            CREATE TABLE olx_journal (id bigserial PRIMARY KEY, cabinet_code text, thread_id text, message_at timestamp,
                                      phone_normalized text, result text, created_at timestamp);
            CREATE TABLE olx_threads (cabinet_code text, thread_id text, interlocutor_name text, advert_title text);
            INSERT INTO olx_journal (cabinet_code, thread_id, message_at, phone_normalized, result, created_at)
                 VALUES ('itaxi', 't-old', '2026-10-01 10:00', '+77055550001', 'lead_created', '2026-10-01 10:00');
        """)
    # Кабинеты «для найма» не выбраны: опрос только ставит закладку на конец журнала и ничего не берёт —
    # кабинеты лидов (таксопарки) к рекрутерам не попадают (владелец 30.10.2026).
    assert service.pull_olx() == 0
    with world['db']._get_cursor() as cur:
        state = queries.get_integration(cur, 'olx')
        assert state['cursor'] == {'journal_id': 1} and state['state'] == 'not_configured'
        assert 'не выбраны' in state['last_error']
        cur.execute("""
            INSERT INTO olx_threads VALUES ('itaxi', 't-new', 'Ерлан', 'Водитель в iTaxi');
            INSERT INTO olx_journal (cabinet_code, thread_id, message_at, phone_normalized, result, created_at) VALUES
                ('itaxi', 't-new', '2026-10-15 11:00', '+77055550002', 'lead_created', '2026-10-15 11:00'),
                ('itaxi', 't-skip', '2026-10-15 11:05', NULL, 'canned_reply', '2026-10-15 11:05'),
                ('jana', 't-dup', '2026-10-15 11:10', '+77055550002', 'duplicate', '2026-10-15 11:10'),
                ('jana', 't-other', '2026-10-15 11:20', '+77055550003', 'lead_created', '2026-10-15 11:20');
        """)
    assert service.pull_olx() == 0            # по-прежнему пусто: закладка уехала на конец журнала
    with world['db']._get_cursor() as cur:
        assert queries.get_integration(cur, 'olx')['cursor'] == {'journal_id': 5}
        assert queries.list_cards(cur, {'lead': True}, SUPER, {'source_id': world['sources']['olx']['id']})[0] == 0
        # Руководитель выбрал кабинет «для найма» — берутся только его чаты, появившиеся после закладки.
        queries.set_integration_settings(cur, 'olx', {'cabinets': ['itaxi']}, NOW)
        cur.execute("""
            INSERT INTO olx_threads VALUES ('itaxi', 't-hire', 'Асель', 'Оператор колл-центра');
            INSERT INTO olx_journal (cabinet_code, thread_id, message_at, phone_normalized, result, created_at) VALUES
                ('itaxi', 't-hire', '2026-10-16 09:00', '+77055550004', 'lead_created', '2026-10-16 09:00'),
                ('jana', 't-park', '2026-10-16 09:05', '+77055550005', 'lead_created', '2026-10-16 09:05'),
                ('itaxi', 't-hire', '2026-10-16 09:30', '+77055550004', 'duplicate', '2026-10-16 09:30');
        """)
    assert service.pull_olx() == 2
    with world['db']._get_cursor() as cur:
        total, rows = queries.list_cards(cur, {'lead': True}, SUPER, {'source_id': world['sources']['olx']['id']})
        assert total == 1
        card = rows[0]
        assert card['full_name'] == 'Асель' and card['vacancy_text'] == 'Оператор колл-центра'
        assert card['platform_ref'] == 'olx:itaxi:t-hire' and card['received_at'] == datetime(2026, 10, 16, 9, 0)
        assert [t['kind'] for t in queries.card_touches(cur, card['id'])] == ['repeat_response']
        assert queries.get_integration(cur, 'olx')['cursor'] == {'journal_id': 8}
        assert queries.open_card_by_phone(cur, '+77055550005') is None   # кабинет лидов — мимо воронки


# ─── Дашборд (ТЗ 8.1) и рабочий стол (ТЗ 3) ───────────────────────────────────

def test_dashboard_cohort_and_click_through(world):
    service = world['service']
    me, trainer, head = ctx_of(world, REC1), ctx_of(world, TRAINER), ctx_of(world, OP_HEAD)
    hired = take_and_touch(world, REC1, '8 705 100 00 01', source_id=world['sources']['hh']['id'])
    service.move(me, hired['id'], 'invited_training', {'group_id': world['group_id']})
    service.move(trainer, hired['id'], 'on_training', {})
    service.move(me, hired['id'], 'on_line', {'line_started_at': '2026-10-20'})
    world['clock'].now = datetime(2026, 11, 2, 12, 0)
    with world['db']._get_cursor() as cur:
        cur.execute("INSERT INTO users (id, name, role, status, department_id, phone) VALUES (78, 'Нанятый Оператор', 'operator', 'working', 367, %s)",
                    (hired['phone'],))
        for day in range(20, 27):
            cur.execute("INSERT INTO daily_hours (operator_id, day, work_time) VALUES (78, %s, 8)", (datetime(2026, 10, day).date(),))
    assert service.sync_shifts()['shifts'] == 7
    service.move(head, hired['id'], 'hired', {})
    world['clock'].now = NOW
    refused = take_and_touch(world, REC2, '8 705 100 00 02')
    with world['db']._get_cursor() as cur:
        reason = queries.reason_by_name(cur, 'refusal_ours', 'Языковой барьер')
    service.move(ctx_of(world, REC2), refused['id'], 'refused', {'initiator': 'ours', 'reason_id': reason['id']})
    _outcome, pool = add_response(world, REC1, '8 705 100 00 03')      # в пуле: поступил, но не взят
    world['clock'].now = datetime(2026, 9, 10, 12, 0)
    take_and_touch(world, REC1, '8 705 100 00 04')                       # сентябрьская когорта
    world['clock'].now = NOW
    with world['db']._get_cursor() as cur:
        queries.create_plan(cur, datetime(2026, 10, 1).date(), world['vacancy_id'], 367, None, 3, None, None, SUPER, NOW)
        filters = reports.parse_filters({'month': '2026-10'})
        data = reports.dashboard(cur, filters, with_ids=True)
        assert data['cohort_size'] == 2
        counts = {row['stage']: row['count'] for row in data['funnel']}
        assert counts == {'in_work': 2, 'dialed': 2, 'invited_office': 0, 'invited_training': 1, 'on_training': 1, 'on_line': 1, 'hired': 1}
        assert [row['through'] for row in data['funnel']][:2] == [100.0, 100.0]
        assert data['funnel'][-1]['through'] == 50.0
        totals = {tile['label']: tile for tile in data['totals']}
        assert totals['Поступило']['value'] == 3
        # План месяца закрывают наймы, подтверждённые в этом месяце (plan_facts): седьмая смена
        # подтверждена 2 ноября — в октябре закрыто 0 FTE, в ноябре 1.
        assert totals['Закрыто FTE плана']['value'] == 0 and totals['Закрыто FTE плана']['plan'] == 3.0
        november = reports.dashboard(cur, reports.parse_filters({'month': '2026-11'}))
        assert {tile['label']: tile['value'] for tile in november['totals']}['Закрыто FTE плана'] == 1
        assert totals['Засчитано мотивации']['value'] == 1
        assert data['losses']['refused']['count'] == 1 and data['losses']['refused']['by_initiator']['ours']['count'] == 1
        assert data['losses']['refused']['reasons'][0]['reason'] == 'Языковой барьер'
        recruiters = {row['label']: row for row in data['by_recruiter']}
        assert recruiters['Первый Рекрутер']['stages']['hired']['count'] == 1
        assert recruiters['Второй Рекрутер']['refused']['count'] == 1
        assert recruiters['Первый Рекрутер']['motivation']['count'] == 1
        sources = {row['label']: row for row in data['by_source']}
        assert sources['HeadHunter']['taken'] == 1 and sources['Вручную']['taken'] == 1
        assert reports.card_ids_for(cur, filters, 'total:hired') == [hired['id']]
        assert set(reports.card_ids_for(cur, filters, 'received')) == {hired['id'], refused['id'], pool['id']}
        assert reports.card_ids_for(cur, filters, 'rec:%s:refused' % REC2) == [refused['id']]
        spans = {row['label']: row for row in data['time_on_stages']}
        assert spans['От выхода на линию до 7 смен']['count'] == 1
        # Фильтр «дата этапа»: выход на линию в октябре — только нанятый.
        narrowed = reports.parse_filters({'month': '2026-10', 'stage': 'on_line', 'stage_from': '2026-10-01', 'stage_to': '2026-10-31'})
        assert reports.dashboard(cur, narrowed)['cohort_size'] == 1
        # Рекрутеру — только по себе.
        own = reports.parse_filters({'month': '2026-10'}, forced_recruiter_id=REC2)
        assert reports.dashboard(cur, own)['cohort_size'] == 1
        # Выгрузка — настоящий xlsx.
        content = reports.export_dashboard(data, 'Воронка найма')
        assert content[:2] == b'PK'
        motivation = reports.motivation_report(cur, reports.parse_filters({'month': '2026-11'}))
        assert motivation[0]['recruiter_name'] == 'Первый Рекрутер' and motivation[0]['counted'] == 1
        assert reports.export_motivation(motivation, '2026-11')[:2] == b'PK'
        # Рабочий стол: воронка рекрутера за месяц и задачи на сегодня.
        funnel = queries.stage_counts_for_recruiter(cur, REC1, datetime(2026, 10, 1), datetime(2026, 11, 1))
        assert funnel['in_work'] == 1 and funnel['hired'] == 1 and funnel['dialed'] == 1
        everyone = queries.stage_counts_for_recruiter(cur, None, datetime(2026, 10, 1), datetime(2026, 11, 1))
        assert everyone['in_work'] == 2
    today_card = take_and_touch(world, REC1, '8 705 100 00 05')
    service.touch(me, today_card['id'], {'kind': 'comment', 'body': 'x', 'next_contact_at': NOW.isoformat()})
    with world['db']._get_cursor() as cur:
        tasks = queries.desk_tasks(cur, REC1, datetime(2026, 10, 15), datetime(2026, 10, 16))
        assert [card['id'] for card in tasks] == [today_card['id']]


def test_dashboard_counts_no_answer_status(world):
    """Владелец 09.11.2026: в «Отчётах» — число «Недозвон / не ответил» (кандидат не ответил ни на звонок, ни на
    сообщение в WhatsApp): кто из взятых в работу за период сейчас в этом статусе; клик — список карточек."""
    from io import BytesIO
    from openpyxl import load_workbook
    service = world['service']
    me, other = ctx_of(world, REC1), ctx_of(world, REC2)

    def taken(user, phone):
        _outcome, card = add_response(world, REC1, phone)
        return service.take(user, card['id'])

    by_call = taken(me, '8 705 200 00 01')                       # не взял трубку — статус ставит сам звонок
    service.touch(me, by_call['id'], {'kind': 'call', 'result': 'no_answer'})
    by_hand = taken(other, '8 705 200 00 02')                    # не ответил в WhatsApp — рекрутер ставит руками
    service.touch(other, by_hand['id'], {'kind': 'whatsapp'})
    service.set_substage(other, by_hand['id'], 'no_answer')
    reached = taken(me, '8 705 200 00 03')                       # потом дозвонились — уже не «не ответил»
    service.touch(me, reached['id'], {'kind': 'call', 'result': 'no_answer'})
    service.touch(me, reached['id'], {'kind': 'call', 'result': 'dial'})
    closed = taken(me, '8 705 200 00 04')                        # закрыт отказом — он в «Потерях»
    service.touch(me, closed['id'], {'kind': 'call', 'result': 'no_answer'})
    with world['db']._get_cursor() as cur:
        no_contact = queries.reason_by_name(cur, 'refusal_candidate', rules.REASON_NO_CONTACT)
    service.move(me, closed['id'], 'refused', {'initiator': 'candidate', 'reason_id': no_contact['id']})
    add_response(world, REC1, '8 705 200 00 05')                 # в пуле — не взят в работу
    world['clock'].now = datetime(2026, 9, 10, 12, 0)            # сентябрьская когорта
    september = taken(me, '8 705 200 00 06')
    service.touch(me, september['id'], {'kind': 'call', 'result': 'no_answer'})
    world['clock'].now = NOW

    with world['db']._get_cursor() as cur:
        filters = reports.parse_filters({'month': '2026-10'})
        data = reports.dashboard(cur, filters)
        labels = [tile['label'] for tile in data['totals']]
        assert labels[labels.index('Взято в работу') + 1] == 'Недозвон / не ответил'
        tile = data['totals'][labels.index('Недозвон / не ответил')]
        assert tile['value'] == 2 and tile['key'] == 'total:no_answer' and 'WhatsApp' in tile['hint']
        assert reports.card_ids_for(cur, filters, 'total:no_answer') == sorted([by_call['id'], by_hand['id']])
        own = reports.dashboard(cur, reports.parse_filters({'month': '2026-10'}, forced_recruiter_id=REC2))
        assert {t['label']: t['value'] for t in own['totals']}['Недозвон / не ответил'] == 1
        assert {t['label']: t['value'] for t in reports.dashboard(cur, reports.parse_filters({'month': '2026-09'}))['totals']}[
            'Недозвон / не ответил'] == 1
        sheet = load_workbook(BytesIO(reports.export_dashboard(data, 'Воронка найма')))['Итоги']
        assert ['Недозвон / не ответил', 2] in [[row[0], row[1]] for row in sheet.iter_rows(values_only=True)]
    # Подпись статуса в карточке — та же; исход самого звонка остаётся «Недозвон».
    assert rules.SUBSTAGE_LABELS['in_work']['no_answer'] == 'Недозвон / не ответил'
    assert dict(rules.CALL_RESULTS)['no_answer'] == 'Недозвон'


# ─── Каналы — метки откликов (владелец 09.11.2026) ─────────────────────────────

def test_channels_directory_and_card_label(world):
    """Владелец 09.11.2026: каждому отклику — метка-канал с цветом (Таргет — синий, hh — красный, Каспи —
    розовый); справочник в «Настройках», где руководитель сам добавляет канал и ставит ему цвет."""
    service = world['service']
    lead, me, other = ctx_of(world, LEAD_GRANT), ctx_of(world, REC1), ctx_of(world, REC2)
    with world['db']._get_cursor() as cur:
        seeded = queries.list_channels(cur)
    # Засев один раз: фикстура разворачивает схему дважды, как перезапуск прода.
    assert [(c['name'], c['color'], c['is_active'], c['position']) for c in seeded] \
        == [('Таргет', 'blue', True, 0), ('hh', 'red', True, 1), ('Каспи', 'pink', True, 2)]
    target, hh_channel = seeded[0], seeded[1]

    # Справочник ведёт руководитель: имя без лишних пробелов, цвет из палитры, без дублей по регистру.
    with pytest.raises(HiringError) as caught:
        service.add_channel(me, {'name': 'OLX', 'color': 'green'})
    assert caught.value.status == 403
    for bad in ({'name': '  ', 'color': 'green'}, {'name': 'OLX', 'color': '#00ff00'}, {'name': 'x' * 41, 'color': 'green'},
                {'name': 'OLX'}):
        with pytest.raises(HiringError) as caught:
            service.add_channel(lead, bad)
        assert caught.value.code == 'HIRING_BAD_CHANNEL'
    with pytest.raises(HiringError) as caught:
        service.add_channel(lead, {'name': 'HH', 'color': 'green'})
    assert caught.value.code == 'HIRING_DUPLICATE' and caught.value.status == 409
    items = service.add_channel(lead, {'name': '  OLX   объявления ', 'color': 'green'})
    olx = items[-1]
    assert (olx['name'], olx['color'], olx['position'], olx['is_active']) == ('OLX объявления', 'green', 3, True)
    items = service.edit_channel(lead, olx['id'], {'name': 'OLX', 'color': 'orange'})
    assert [(c['name'], c['color']) for c in items if c['id'] == olx['id']] == [('OLX', 'orange')]
    with pytest.raises(HiringError) as caught:
        service.edit_channel(lead, olx['id'], {'name': 'hh'})
    assert caught.value.code == 'HIRING_DUPLICATE'
    with pytest.raises(HiringError) as caught:
        service.edit_channel(lead, olx['id'], {'color': 'black'})
    assert caught.value.code == 'HIRING_BAD_CHANNEL'
    with pytest.raises(HiringError) as caught:
        service.edit_channel(lead, 999999, {'color': 'red'})
    assert caught.value.status == 404
    with pytest.raises(HiringError) as caught:
        service.edit_channel(me, olx['id'], {'is_active': False})
    assert caught.value.status == 403

    # Метку ставит закреплённый рекрутер или руководитель; карточка отдаёт имя и цвет канала.
    _outcome, card = add_response(world, REC1, '8 705 300 00 01')
    with pytest.raises(HiringError) as caught:            # в пуле карточку правит только руководитель
        service.edit_card(me, card['id'], {'channel_id': target['id']})
    assert caught.value.status == 403
    assert service.edit_card(lead, card['id'], {'channel_id': target['id']})['channel_name'] == 'Таргет'
    card = service.take(me, card['id'])
    assert (card['channel_id'], card['channel_name'], card['channel_color']) == (target['id'], 'Таргет', 'blue')
    card = service.edit_card(me, card['id'], {'channel_id': str(hh_channel['id'])})
    assert (card['channel_name'], card['channel_color']) == ('hh', 'red')
    with pytest.raises(HiringError) as caught:
        service.edit_card(other, card['id'], {'channel_id': target['id']})
    assert caught.value.code == 'HIRING_NOT_FOUND'
    for bad in ('abc', 999999, True):
        with pytest.raises(HiringError) as caught:
            service.edit_card(me, card['id'], {'channel_id': bad})
        assert caught.value.code == 'HIRING_BAD_CHANNEL'

    # Выключенный канал новым карточкам не ставится, а у помеченных остаётся — и с новым цветом.
    service.edit_channel(lead, hh_channel['id'], {'is_active': False, 'color': 'violet'})
    with world['db']._get_cursor() as cur:
        assert [c['name'] for c in queries.list_channels(cur, active_only=True)] == ['Таргет', 'Каспи', 'OLX']
        kept = queries.get_card(cur, card['id'])
        assert (kept['channel_name'], kept['channel_color']) == ('hh', 'violet')
    assert service.edit_card(me, card['id'], {'channel_id': hh_channel['id'], 'city': 'Астана'})['channel_id'] == hh_channel['id']
    _outcome, fresh = add_response(world, REC1, '8 705 300 00 02')
    fresh = service.take(me, fresh['id'])
    with pytest.raises(HiringError) as caught:
        service.edit_card(me, fresh['id'], {'channel_id': hh_channel['id']})
    assert caught.value.code == 'HIRING_BAD_CHANNEL'
    # Имя выключенного канала включает его заново, а не заводит второй с тем же именем.
    items = service.add_channel(lead, {'name': 'HH', 'color': 'red'})
    assert len(items) == 4
    assert [(c['name'], c['color'], c['is_active']) for c in items if c['id'] == hh_channel['id']] == [('HH', 'red', True)]
    # Снять метку — None или пустое значение.
    assert service.edit_card(me, card['id'], {'channel_id': None})['channel_id'] is None
    assert service.edit_card(me, fresh['id'], {'channel_id': target['id']})['channel_id'] == target['id']
    assert service.edit_card(me, fresh['id'], {'channel_id': ''})['channel_name'] is None

    # Доска и рабочий стол отдают метку вместе с карточкой.
    service.edit_card(me, fresh['id'], {'channel_id': target['id'], 'next_contact_at': NOW.isoformat()})
    with world['db']._get_cursor() as cur:
        columns, counts = queries.board(cur, {'lead': True}, LEAD_GRANT, {})
        assert counts['in_work'] == 2
        assert [(c['channel_name'], c['channel_color']) for c in columns['in_work'] if c['id'] == fresh['id']] == [('Таргет', 'blue')]
        tasks = queries.desk_tasks(cur, REC1, datetime(2026, 10, 15), datetime(2026, 10, 16))
        assert [(c['id'], c['channel_name']) for c in tasks] == [(fresh['id'], 'Таргет')]


TARGET_AD = {'title': 'Напишите нам!', 'body': 'Требуются операторы', 'source_id': '120250535993810050',
             'source_app': 'instagram', 'conversion_source': 'FB_Ads', 'entry_point': 'ctwa_ad', 'ctwa_clid': 'x'}


def test_target_label_is_set_automatically(world):
    """Владелец 09.11.2026: «Те что пришли с таргета нужно автоматом отмечать как Таргет»: отклик WhatsApp по рекламе и
    отклик с источником «Таргет» получают метку сами. Выбор рекрутера не перетирается; переименованный «Таргет»
    ставится по-прежнему (по коду), выключенный — нет."""
    service = world['service']
    lead, me = ctx_of(world, LEAD_GRANT), ctx_of(world, REC1)
    with world['db']._get_cursor() as cur:
        target, hh_channel = queries.channel_by_code(cur, 'target'), queries.channel_by_code(cur, 'hh')
    assert (target['name'], target['color'], hh_channel['name']) == ('Таргет', 'blue', 'hh')

    def label(phone):
        with world['db']._get_cursor() as cur:
            return queries.open_card_by_phone(cur, phone)['channel_name']

    # WhatsApp: по объявлению — «Таргет»; по ссылке на номер (так пишут с hh, сайта) и без рекламы — без метки.
    service.wa_events({'events': [wa_event('g1', phone='77055553001', ad=TARGET_AD),
                                  wa_event('g2', phone='77055553002', ad={'entry_point': 'phone_number_hyperlink'}),
                                  wa_event('g3', phone='77055553003')]})
    assert [label('+7705555300%d' % n) for n in (1, 2, 3)] == ['Таргет', None, None]
    # Источник «Таргет» — вебхук партнёра и отклик вручную; Kaspi — без метки (её ставит рекрутер).
    results = service.external_intake([{'phone': '8 705 555 30 04', 'name': 'Из рекламы'}], source_code='target',
                                      actor_label='Партнёр: тест')
    assert results[0]['status'] == 'created' and label('+77055553004') == 'Таргет'
    service.external_intake([{'phone': '8 705 555 30 05'}], source_code='kaspi', actor_label='Партнёр: тест')
    assert label('+77055553005') is None
    add_response(world, REC1, '8 705 555 30 06', source_id=world['sources']['target']['id'])
    assert label('+77055553006') == 'Таргет'

    # Повторный отклик по рекламе: карточке без метки — «Таргет», метку рекрутера не трогает.
    _outcome, base = add_response(world, REC1, '8 705 555 30 07')
    _outcome, chosen = add_response(world, REC1, '8 705 555 30 08')
    service.take(me, chosen['id'])
    service.edit_card(me, chosen['id'], {'channel_id': hh_channel['id']})
    assert service.wa_events({'events': [wa_event('g7', phone='77055553007', ad=TARGET_AD),
                                         wa_event('g8', phone='77055553008', ad=TARGET_AD)]})['outcomes'] == {'repeat': 2}
    assert (label('+77055553007'), label('+77055553008')) == ('Таргет', 'hh')

    # Переименование не ломает автометку (канал — по коду); выключенный канал сам не ставится.
    service.edit_channel(lead, target['id'], {'name': 'Реклама'})
    service.wa_events({'events': [wa_event('g9', phone='77055553009', ad=TARGET_AD)]})
    assert label('+77055553009') == 'Реклама'
    service.edit_channel(lead, target['id'], {'is_active': False})
    service.wa_events({'events': [wa_event('g10', phone='77055553010', ad=TARGET_AD)]})
    assert label('+77055553010') is None


def test_target_backfill_runs_once_when_the_code_appears(world):
    """Выкладка 09.11.2026: справочник засеян до кодов. На первом старте с кодами «Таргет» получает код, и уже
    пришедшие с таргета (WhatsApp по рекламе, повторный отклик по рекламе, источник «Таргет») размечаются один раз:
    метки рекрутера не трогаются, а снятая потом метка следующим стартом не возвращается."""
    service = world['service']
    me = ctx_of(world, REC1)
    with world['db']._get_cursor() as cur:
        cur.execute("UPDATE hiring_channels SET code = NULL")          # как засеяла выкладка 40037a92
        hh_id = next(c['id'] for c in queries.list_channels(cur) if c['name'] == 'hh')
    service.wa_events({'events': [wa_event('h1', phone='77055554001', ad=TARGET_AD), wa_event('h2', phone='77055554002')]})
    add_response(world, REC1, '8 705 555 40 03', source_id=world['sources']['target']['id'])
    add_response(world, REC1, '8 705 555 40 04')
    service.wa_events({'events': [wa_event('h4', phone='77055554004', ad=TARGET_AD)]})     # повторный отклик по рекламе
    _outcome, chosen = add_response(world, REC1, '8 705 555 40 05', source_id=world['sources']['target']['id'])
    service.take(me, chosen['id'])
    service.edit_card(me, chosen['id'], {'channel_id': hh_id})                            # рекрутер решил сам
    with world['db']._get_cursor() as cur:
        def label(phone):
            return queries.open_card_by_phone(cur, phone)['channel_name']

        assert [label('+7705555400%d' % n) for n in range(1, 6)] == [None, None, None, None, 'hh']
        cur.execute("SELECT updated_at FROM hiring_cards WHERE phone = '+77055554001'")
        updated_before = cur.fetchone()[0]
        schema.init_hiring_schema(cur)
        assert {c['name']: c['code'] for c in queries.list_channels(cur)} == {'Таргет': 'target', 'hh': 'hh', 'Каспи': 'kaspi'}
        assert [label('+7705555400%d' % n) for n in range(1, 6)] == ['Таргет', None, 'Таргет', 'Таргет', 'hh']
        cur.execute("SELECT updated_at FROM hiring_cards WHERE phone = '+77055554001'")
        assert cur.fetchone()[0] == updated_before                                            # доска не перемешивается
        cur.execute("UPDATE hiring_cards SET channel_id = NULL WHERE phone = '+77055554001'")
        schema.init_hiring_schema(cur)
        assert label('+77055554001') is None


# ─── Звонки из телефонии Binotel (владелец 30.10.2026) ────────────────────────

def _webhook(general_call_id, phone, *, disposition='ANSWER', billsec=120, call_type='1', line='8501', start=1792000000):
    return {'callDetails[generalCallID]': str(general_call_id), 'callDetails[callType]': call_type,
            'callDetails[internalNumber]': line, 'callDetails[externalNumber]': phone,
            'callDetails[disposition]': disposition, 'callDetails[billsec]': str(billsec), 'callDetails[waitsec]': '7',
            'callDetails[startTime]': str(start), 'callDetails[recordingStatus]': '1'}


class _FakeBinotelClient:
    def __init__(self, history):
        self.history = history
        self.record_requests = []

    def list_calls_by_internal_number(self, internal_number, start_ts, stop_ts, max_wait=None):
        return [row for row in self.history if row['internal_number'] == internal_number]

    def get_call_record_url(self, general_call_id, max_wait=None):
        self.record_requests.append(general_call_id)
        return 'https://records.test/%s.mp3' % general_call_id


class _FakeDialList:
    def __init__(self, client):
        self.client = client
        self.settings = {}

    def client_for_department(self, department_id):
        return self.client

    def user_setting(self, user_id):
        return self.settings.get(int(user_id))

    def save_user_setting(self, user_id, enabled, changed_by=None):
        self.settings[int(user_id)] = enabled


def test_binotel_calls_land_in_cards(world):
    """Звонок с линии рекрутера — касание в карточке с исходом, длительностью и записью; ручная отметка
    рядом по времени дописывается, а не дублируется; входящий — тоже; чужая линия и чужой номер — мимо."""
    service, clock = world['service'], world['clock']
    me = ctx_of(world, REC1)
    card = take_and_touch(world, REC1, '8 705 500 00 01')
    ext = '8' + card['phone'][2:]        # как Binotel пишет externalNumber: 8 + десять цифр
    with world['db']._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_line_members (user_id, department_id, internal_number) VALUES (%s, 367, '8501')", (REC1,))
    # Телефония: разговор 2 минуты — касание «дозвон», подстатус «Дозвон», запись доступна.
    assert service.on_binotel_call(_webhook('c-1', ext), matched=False)
    with world['db']._get_cursor() as cur:
        rows = queries.card_calls(cur, card['id'])
        assert len(rows) == 1 and rows[0]['result'] == 'answered' and rows[0]['billsec'] == 120
        assert rows[0]['recording'] == 'available' and rows[0]['direction'] == 'out' and rows[0]['recruiter_id'] == REC1
        touches = queries.card_touches(cur, card['id'])
        last = touches[-1]
        assert last['kind'] == 'call' and last['result'] == 'dial' and last['duration_sec'] == 120
        assert last['body'].startswith('Исходящий звонок · Дозвонились · 2:00') and last['call_id'] == rows[0]['id']
        fresh = queries.get_card(cur, card['id'])
        assert fresh['substage'] == 'dial' and fresh['no_answer_streak'] == 0
    # Тот же звонок второй раз (история после вебхука) — без дубля.
    assert service.on_binotel_call(_webhook('c-1', ext), matched=False)
    with world['db']._get_cursor() as cur:
        assert len(queries.card_calls(cur, card['id'])) == 1
        assert sum(1 for t in queries.card_touches(cur, card['id']) if t['kind'] == 'call') == 2   # первый — из take_and_touch
    # Звонок обзвона (matched) и звонок ещё идёт — не наши.
    assert service.on_binotel_call(_webhook('c-2', ext), matched=True) is None
    assert service.on_binotel_call(_webhook('c-3', ext, disposition='ONLINE'), matched=False) is None
    # Ручная отметка «недозвон» сразу после «Позвонить», потом вебхук того же звонка — одна строка.
    clock.now = NOW + timedelta(hours=1)
    manual = service.touch(me, card['id'], {'kind': 'call', 'result': 'no_answer', 'body': 'не берёт'})
    started = int((clock.now - timedelta(minutes=2)).replace(tzinfo=rules.ALMATY).timestamp())
    assert service.on_binotel_call(_webhook('c-4', ext, disposition='NOANSWER', billsec=0, start=started), matched=False)
    with world['db']._get_cursor() as cur:
        touches = [t for t in queries.card_touches(cur, card['id']) if t['kind'] == 'call']
        assert len(touches) == 3
        merged = touches[-1]
        assert merged['call_id'] and merged['result'] == 'no_answer' and 'не берёт' in merged['body']
        assert merged['body'].startswith('Исходящий звонок · Не поднял трубку')
        assert queries.get_card(cur, card['id'])['no_answer_streak'] == 1    # ручная отметка уже посчитала
    # Входящий от кандидата на линию рекрутера — тоже в карточку.
    assert service.on_binotel_call(_webhook('c-5', ext, call_type='0', billsec=30, start=1792052000), matched=False)
    with world['db']._get_cursor() as cur:
        incoming = [row for row in queries.card_calls(cur, card['id']) if row['direction'] == 'in']
        assert len(incoming) == 1 and incoming[0]['result'] == 'answered'
    # Чужая линия и неизвестный номер — мимо.
    assert service.on_binotel_call(_webhook('c-6', ext, line='8502'), matched=False) is None
    assert service.on_binotel_call(_webhook('c-7', '87055559999'), matched=False) is None
    # Добор истории по линии рекрутера — те же правила, новые звонки появляются, старые не дублируются.
    client = _FakeBinotelClient([
        {'general_call_id': 'c-1', 'call_type': 1, 'internal_number': '8501', 'external_number': ext,
         'disposition': 'ANSWER', 'billsec': 120, 'waitsec': 7, 'start_time': 1792000000},
        {'general_call_id': 'c-8', 'call_type': 1, 'internal_number': '8501', 'external_number': ext,
         'disposition': 'BUSY', 'billsec': 0, 'waitsec': 3, 'start_time': 1792053000},
        {'general_call_id': 'c-9', 'call_type': 1, 'internal_number': '8599', 'external_number': ext,
         'disposition': 'ANSWER', 'billsec': 50, 'waitsec': 3, 'start_time': 1792053000},
    ])
    fake_lines = _FakeDialList(client)
    service._dial_list = lambda: fake_lines
    assert service.sync_call_history() == 1
    assert fake_lines.settings == {REC1: False}      # посаженному раньше рекрутеру обзвон выключен (обычная учётка)
    with world['db']._get_cursor() as cur:
        rows = queries.card_calls(cur, card['id'])
        assert [row['general_call_id'] for row in rows] == ['c-1', 'c-4', 'c-5', 'c-8']
        assert rows[-1]['result'] == 'busy' and rows[-1]['source'] == 'history'
        state = queries.get_integration(cur, 'binotel')
        assert state['state'] == 'ok' and state['cursor'].get('synced_at')
    # Запись разговора — тому, кто видит карточку; для недозвона записи нет.
    answered = next(row for row in rows if row['general_call_id'] == 'c-1')
    link = service.recording_url(me, answered['id'])
    assert link['url'] == 'https://records.test/c-1.mp3' and link['billsec'] == 120 and client.record_requests == ['c-1']
    busy = next(row for row in rows if row['general_call_id'] == 'c-8')
    with pytest.raises(HiringError) as caught:
        service.recording_url(me, busy['id'])
    assert caught.value.code == 'HIRING_NO_RECORDING'
    with pytest.raises(HiringError) as caught:
        service.recording_url(ctx_of(world, REC2), answered['id'])
    assert caught.value.code == 'HIRING_NOT_FOUND'


def test_pending_call_waits_for_telephony(world):
    """«Позвонить» без исхода (владелец 30.10.2026: исход подтягивается сам, рекрутер дописывает
    недостающее): касание ждёт телефонию, вебхук дописывает исход, подстатус и серию недозвонов;
    без телефонии исход ставят руками; исход из телефонии руками не переписать."""
    service, clock = world['service'], world['clock']
    me = ctx_of(world, REC1)
    card = take_and_touch(world, REC1, '8 705 500 00 02')
    with world['db']._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_line_members (user_id, department_id, internal_number) VALUES (%s, 367, '8503')", (REC1,))
    clock.now = NOW + timedelta(hours=2)
    # «Позвонить» нажал руководитель из своей учётки, звонок уйдёт с линии рекрутера — слияние всё равно в это касание.
    before = service.touch(ctx_of(world, HR_HEAD), card['id'], {'kind': 'call'})
    assert before['substage'] == 'dial' and before['no_answer_streak'] == 0      # исхода ещё нет — ничего не решено
    with world['db']._get_cursor() as cur:
        pending = queries.card_touches(cur, card['id'])[-1]
        assert pending['kind'] == 'call' and pending['result'] is None and pending['call_id'] is None
    # Пока телефония молчит, рекрутер дописывает комментарий и следующий контакт.
    edited = service.edit_touch(me, card['id'], pending['id'], {'body': 'перезвонить вечером', 'next_contact_at': '2026-10-16T18:00'})
    assert edited['next_contact_at'] == datetime(2026, 10, 16, 18, 0)
    # Телефония: не поднял трубку — исход в то же касание, подстатус и серия как у ручной отметки.
    started = int((clock.now + timedelta(seconds=20)).replace(tzinfo=rules.ALMATY).timestamp())
    assert service.on_binotel_call(_webhook('p-1', '8' + card['phone'][2:], disposition='NOANSWER', billsec=0, line='8503', start=started), matched=False)
    with world['db']._get_cursor() as cur:
        filled = next(t for t in queries.card_touches(cur, card['id']) if t['id'] == pending['id'])
        assert filled['result'] == 'no_answer' and filled['call_id'] and 'перезвонить вечером' in filled['body']
        fresh = queries.get_card(cur, card['id'])
        assert fresh['substage'] == 'no_answer' and fresh['no_answer_streak'] == 1
    with pytest.raises(HiringError) as caught:
        service.edit_touch(me, card['id'], pending['id'], {'result': 'dial'})
    assert caught.value.code == 'HIRING_TOUCH_FROM_PBX'
    # Звонок мимо телефонии: исход ставят руками — подстатус и серия считаются.
    clock.now = NOW + timedelta(hours=3)
    service.touch(me, card['id'], {'kind': 'call'})
    with world['db']._get_cursor() as cur:
        manual = queries.card_touches(cur, card['id'])[-1]
    after = service.edit_touch(me, card['id'], manual['id'], {'result': 'no_answer'})
    assert after['substage'] == 'no_answer' and after['no_answer_streak'] == 2
    after = service.edit_touch(me, card['id'], manual['id'], {'result': 'dial'})
    assert after['substage'] == 'dial' and after['no_answer_streak'] == 0
    with pytest.raises(HiringError) as caught:
        service.edit_touch(me, card['id'], manual['id'], {'result': 'maybe'})
    assert caught.value.code == 'HIRING_BAD_CALL_RESULT'
    with pytest.raises(HiringError) as caught:
        service.edit_touch(ctx_of(world, REC2), card['id'], manual['id'], {'body': 'чужое'})
    assert caught.value.code == 'HIRING_NOT_FOUND'
    # Ожидание, мимо которого телефония уже завела свою запись (как было до правки слияния): джоба
    # переносит звонок в ожидание и убирает лишнюю запись; комментарий ожидания остаётся.
    clock.now = NOW + timedelta(hours=4)
    orphan = service.touch(me, card['id'], {'kind': 'call', 'body': 'ждал исход'})
    with world['db']._get_cursor() as cur:
        orphan_id = queries.card_touches(cur, card['id'])[-1]['id']
        stray_at = clock.now + timedelta(minutes=1)
        stray_call = {'general_call_id': 'p-9', 'internal_number': '8503', 'external_phone': card['phone'], 'direction': 'out',
                      'disposition': 'ANSWER', 'billsec': 50, 'waitsec': 3, 'started_at': stray_at}
        stray_call_id = queries.insert_call(cur, stray_call, card['id'], REC1, 367, 'answered', 'available', 'webhook', stray_at)
        stray_touch = queries.add_touch(cur, card['id'], 'call', 'dial', 'Исходящий звонок · Дозвонились · 0:50', REC1, 'Первый Рекрутер',
                                        stray_at, duration_sec=50, meta={'general_call_id': 'p-9'})
        queries.link_call_touch(cur, stray_call_id, stray_touch)
    clock.now = NOW + timedelta(hours=4, minutes=10)
    assert service.adopt_orphan_pending_calls() == 1
    with world['db']._get_cursor() as cur:
        touches = queries.card_touches(cur, card['id'])
        assert all(t['id'] != stray_touch for t in touches)
        adopted = next(t for t in touches if t['id'] == orphan_id)
        assert adopted['result'] == 'dial' and adopted['call_id'] == stray_call_id and adopted['duration_sec'] == 50
        assert adopted['body'] == 'Исходящий звонок · Дозвонились · 0:50\nждал исход' and adopted['meta'] == {'general_call_id': 'p-9'}
        assert queries.get_call(cur, stray_call_id)['touch_id'] == orphan_id
    assert service.adopt_orphan_pending_calls() == 0
    assert orphan['substage'] == 'dial'


# ─── Номер WhatsApp найма (сервис на шлюзе, hiring/whatsapp.py) ───────────────

WA_TS = 1792047600          # 2026-10-15 12:00 по Алматы — как NOW


def wa_event(message_id, phone='77055552233', **extra):
    base = {'type': 'message', 'id': message_id, 'chat': '%s@s.whatsapp.net' % phone, 'phone': phone,
            'from_me': False, 'ts': WA_TS, 'push_name': 'Айгерим', 'kind': 'text', 'text': 'Здравствуйте'}
    base.update(extra)
    return base


def test_whatsapp_first_message_becomes_response(world):
    service = world['service']
    ad = {'title': 'Напишите нам!', 'body': 'Требуются операторы', 'source_id': '120208', 'source_app': 'instagram',
          'ctwa_clid': 'abc'}
    result = service.wa_events({'events': [wa_event('m1', text='Хочу работать', ad=ad)]})
    assert result == {'accepted': 1, 'outcomes': {'created': 1}}
    with world['db']._get_cursor() as cur:
        card = queries.open_card_by_phone(cur, '+77055552233')
        assert card['stage'] == 'responses' and card['full_name'] == 'Айгерим' and card['received_at'] == NOW
        assert card['campaign'] == 'Instagram · объявление 120208 · Требуются операторы'
        assert card['comment'] == 'WhatsApp: «Хочу работать»'
        # Ссылка на площадку — без телефона: её видят и удалённые рекрутеры, которым телефон пула скрыт.
        assert card['platform_ref'] == 'wa:m1'
        assert queries.get_source(cur, card['source_id'])['code'] == 'whatsapp'
        chat = queries.wa_get_chat(cur, '+77055552233')
        assert chat['card_id'] == card['id'] and chat['messages'] == 1
        cur.execute("SELECT user_id FROM hiring_notifications WHERE kind = 'new_response' AND card_id = %s ORDER BY user_id",
                    (card['id'],))
        assert [row[0] for row in cur.fetchall()] == [REC1, REC2, REMOTE]
        assert queries.get_integration(cur, 'whatsapp')['received_total'] == 1
    # Дальше чат открытой карточки — только переписка: ни отклика, ни уведомлений. Ответ с телефона
    # (from_me) имя кандидата не перетирает; пока не выбран «Отвечает с телефона», в работу не берёт.
    # Повтор доставки — тот же «m1» — не дублируется.
    again = service.wa_events({'events': [wa_event('m2', text='Есть вакансия?'),
                                          wa_event('m3', from_me=True, push_name='Найм', text='Да, есть', ts=WA_TS + 120),
                                          wa_event('m1', text='Хочу работать')]})
    assert again['outcomes'] == {'stored': 2, 'duplicate': 1}
    with world['db']._get_cursor() as cur:
        assert queries.card_touches(cur, card['id']) == []
        cur.execute("SELECT COUNT(*) FROM hiring_notifications WHERE card_id = %s", (card['id'],))
        assert cur.fetchone()[0] == 3
        chat = queries.wa_get_chat(cur, '+77055552233')
        assert chat['messages'] == 3 and chat['push_name'] == 'Айгерим'
        assert queries.get_integration(cur, 'whatsapp')['received_total'] == 1
    thread = service.card_whatsapp(ctx_of(world, HR_HEAD), card['id'])
    assert [m['wa_id'] for m in thread['messages']] == ['m1', 'm2', 'm3']
    assert [m['from_me'] for m in thread['messages']] == [False, False, True]
    assert thread['messages'][0]['ad']['ctwa_clid'] == 'abc'
    assert thread['messages'][0]['ad_label'] == 'Instagram · объявление 120208 · Требуются операторы'
    assert 'ad_label' not in thread['messages'][1]


def test_whatsapp_phone_reply_takes_card_into_work(world):
    """Владелец 09.11.2026: отклики WhatsApp висели в «Откликах», хотя рекрутер уже переписывался с
    телефона. Первый ответ с телефона берёт карточку в работу к выбранному «Отвечает с телефона»,
    каждый день переписки — касание WhatsApp; автоответ WhatsApp Business не считается."""
    service = world['service']
    lead = ctx_of(world, HR_HEAD)
    service.wa_events({'events': [wa_event('a1', text='Здравствуйте, по вакансии')]})
    # Автоответ (через 5 с после кандидата) и ответ человека, пока отвечающий не выбран, — карточка в пуле.
    assert service.wa_events({'events': [wa_event('a2', from_me=True, ts=WA_TS + 5, text='Спасибо! Ответим скоро')]}) \
        == {'accepted': 1, 'outcomes': {'auto_reply': 1}}
    assert service.wa_events({'events': [wa_event('a3', from_me=True, ts=WA_TS + 300, text='Добрый день! Удобно созвониться?')]}) \
        == {'accepted': 1, 'outcomes': {'stored': 1}}
    with world['db']._get_cursor() as cur:
        card = queries.open_card_by_phone(cur, '+77055552233')
    assert card['stage'] == 'responses' and card['recruiter_id'] is None
    # Отвечающего выбирает руководитель — рекрутер и посторонний не могут, чужого выбрать нельзя.
    with pytest.raises(HiringError) as forbidden:
        service.wa_settings(ctx_of(world, REC1), {'phone_recruiter_id': REC1})
    assert forbidden.value.status == 403
    with pytest.raises(HiringError) as stranger:
        service.wa_settings(lead, {'phone_recruiter_id': STRANGER})
    assert stranger.value.code == 'HIRING_BAD_RECRUITER'
    options = {row['id'] for row in service.wa_status(lead)['recruiter_options']}
    assert {REC1, REC2, REMOTE, LEAD_GRANT, HR_HEAD} <= options and STRANGER not in options and SUPER not in options
    # Выбор применяется к уже идущей переписке: карточка уходит в работу со временем первого ответа
    # человека (a3), автоответ a2 пропущен.
    shown = service.wa_settings(lead, {'phone_recruiter_id': str(REC1)})
    assert shown['applied'] == 1 and shown['phone_recruiter_id'] == REC1
    reply_at = NOW + timedelta(minutes=5)
    with world['db']._get_cursor() as cur:
        card = queries.get_card(cur, card['id'])
        assert (card['stage'], card['substage'], card['recruiter_id']) == ('in_work', 'whatsapp', REC1)
        assert card['taken_at'] == reply_at and card['last_touch_at'] == reply_at and card['candidate_no']
        touches = queries.card_touches(cur, card['id'])
        assert [(t['kind'], t['author_id'], t['at'], t['meta']['source'], t['meta']['wa_id']) for t in touches] \
            == [('whatsapp', REC1, reply_at, 'wa_phone', 'a3')]
        assert touches[0]['body'] == 'Ответ с телефона найма: «Добрый день! Удобно созвониться?»'
        assistant = queries.card_assignments(cur, card['id'])
        assert [(a['recruiter_id'], a['reason'], a['comment']) for a in assistant] == [(REC1, 'take', 'Ответ с телефона найма')]
        assert [s['stage'] for s in queries.card_stages(cur, card['id'])] == ['responses', 'in_work']
    # Повторный выбор того же — ничего не задваивает.
    assert service.wa_settings(lead, {'phone_recruiter_id': REC1})['applied'] == 0
    # Ещё ответ в тот же день — касания нет (одно в день); на следующий день — новое.
    assert service.wa_events({'events': [wa_event('a4', from_me=True, ts=WA_TS + 3600, text='Жду звонка')]})['outcomes'] == {'stored': 1}
    assert service.wa_events({'events': [wa_event('a5', from_me=True, ts=WA_TS + 86400, text='Вы придёте?')]})['outcomes'] == {'touched': 1}
    with world['db']._get_cursor() as cur:
        assert [t['meta']['wa_id'] for t in queries.card_touches(cur, card['id'])] == ['a3', 'a5']
        assert queries.get_card(cur, card['id'])['last_touch_at'] == NOW + timedelta(days=1)
    # Новый чат при выбранном отвечающем — первый ответ берёт в работу сразу.
    service.wa_events({'events': [wa_event('b1', phone='77055554455', text='Добрый вечер')]})
    assert service.wa_events({'events': [wa_event('b2', phone='77055554455', from_me=True, ts=WA_TS + 60, text='Здравствуйте!')]}) \
        == {'accepted': 1, 'outcomes': {'taken': 1}}
    # Карточку другого рекрутера ответ с телефона не перебирает — касание пишется за закреплённым.
    outcome, other = add_response(world, REC2, '8 705 555 66 77', full_name='Сидоров')
    other = service.take(ctx_of(world, REC2), other['id'])
    service.wa_events({'events': [wa_event('c1', phone='77055556677', ts=WA_TS + 7200)]})
    assert service.wa_events({'events': [wa_event('c2', phone='77055556677', from_me=True, ts=WA_TS + 7400)]})['outcomes'] \
        == {'touched': 1}
    with world['db']._get_cursor() as cur:
        card = queries.get_card(cur, other['id'])
        assert card['recruiter_id'] == REC2
        assert [(t['kind'], t['author_id']) for t in queries.card_touches(cur, other['id']) if t['kind'] == 'whatsapp'] \
            == [('whatsapp', REC2)]
        # Завершённая карточка не трогается.
        cur.execute("UPDATE hiring_cards SET is_closed = TRUE, closed_at = %s WHERE id = %s", (NOW, other['id']))
    assert service.wa_events({'events': [wa_event('c3', phone='77055556677', from_me=True, ts=WA_TS + 90000)]})['outcomes'] \
        == {'stored': 1}
    # Напоминание с телефона ушло раньше, чем человек написал (переписка до подключения номера), —
    # карточка берётся в работу первым ответом ПОСЛЕ отклика, а не задним числом раньше него.
    service.wa_settings(lead, {'phone_recruiter_id': None})
    service.wa_events({'events': [wa_event('e1', phone='77055559900', from_me=True, ts=WA_TS - 3600, text='Напоминаю про обучение')]})
    service.wa_events({'events': [wa_event('e2', phone='77055559900', text='Да, приду')]})
    service.wa_events({'events': [wa_event('e3', phone='77055559900', from_me=True, ts=WA_TS + 600, text='Ждём')]})
    assert service.wa_settings(lead, {'phone_recruiter_id': REC1})['applied'] == 1
    with world['db']._get_cursor() as cur:
        late = queries.open_card_by_phone(cur, '+77055559900')
        assert late['taken_at'] == NOW + timedelta(minutes=10)
        assert [t['meta']['wa_id'] for t in queries.card_touches(cur, late['id']) if t['kind'] == 'whatsapp'] == ['e3']
    # Сняли отвечающего — новые ответы карточки из пула не берут.
    service.wa_settings(lead, {'phone_recruiter_id': None})
    service.wa_events({'events': [wa_event('d1', phone='77055557788')]})
    assert service.wa_events({'events': [wa_event('d2', phone='77055557788', from_me=True, ts=WA_TS + 60)]})['outcomes'] \
        == {'stored': 1}


def test_whatsapp_our_message_lets_card_leave_in_work(world):
    """Владелец 09.11.2026: переписку WhatsApp мы видим сами — если с нашей стороны есть хотя бы одно
    сообщение человеку, переход из «В работе» не требует «Позвонить» или «Написать в WhatsApp».
    Автоответ WhatsApp Business, удалённое у всех и переписка до отклика — не в счёт; после повторной
    активации — только новое сообщение, как и остальные касания."""
    service, clock = world['service'], world['clock']
    me, lead = ctx_of(world, REC1), ctx_of(world, LEAD_GRANT)
    office = {'office_visit_at': '2026-10-16T10:00'}
    # Карточка не из WhatsApp, чат рекрутер начинает сам: чат к карточке не привязан, касаний не пишет.
    service.wa_events({'events': [wa_event('s0', phone='77055551899', from_me=True, ts=WA_TS - 3600, text='Помните нас?')]})
    _outcome, card = add_response(world, REC1, '8 705 555 18 99')
    card = service.take(me, card['id'])
    with pytest.raises(HiringError) as caught:          # сообщение раньше отклика — не связь по этой карточке
        service.move(me, card['id'], 'invited_office', office)
    assert caught.value.code == 'HIRING_NO_TOUCH'
    service.wa_events({'events': [wa_event('s1', phone='77055551899', from_me=True, ts=WA_TS + 600, text='Здравствуйте! По вакансии')]})
    card = service.move(me, card['id'], 'invited_office', office)
    assert card['stage'] == 'invited_office'
    with world['db']._get_cursor() as cur:
        assert [t['kind'] for t in queries.card_touches(cur, card['id'])] == []

    # Отклик WhatsApp: рекрутер ответил с телефона, пока карточка ждала в пуле («Отвечает с телефона» не
    # выбран), в работу взял позже — ответ всё равно засчитан. У соседа был только автоответ — нет.
    service.wa_events({'events': [wa_event('t1', phone='77055551011', text='Хочу работать'),
                                  wa_event('t2', phone='77055551011', from_me=True, ts=WA_TS + 5, text='Спасибо за обращение!'),
                                  wa_event('t3', phone='77055551011', from_me=True, ts=WA_TS + 300, text='Добрый день! Удобно созвониться?'),
                                  wa_event('u1', phone='77055551022', text='Хочу работать'),
                                  wa_event('u2', phone='77055551022', from_me=True, ts=WA_TS + 5, text='Спасибо за обращение!')]})
    clock.now = NOW + timedelta(hours=1)
    with world['db']._get_cursor() as cur:
        answered = queries.open_card_by_phone(cur, '+77055551011')
        silent = queries.open_card_by_phone(cur, '+77055551022')
    service.take(me, answered['id'])
    service.take(me, silent['id'])
    with world['db']._get_cursor() as cur:
        assert queries.card_touches(cur, answered['id']) == []
    assert service.move(me, answered['id'], 'invited_office', office)['stage'] == 'invited_office'
    with pytest.raises(HiringError) as caught:
        service.move(me, silent['id'], 'invited_office', office)
    assert caught.value.code == 'HIRING_NO_TOUCH'
    # Сообщение, удалённое у всех, — как не отправленное.
    _outcome, gone = add_response(world, REC1, '8 705 555 10 44')
    service.take(me, gone['id'])
    service.wa_events({'events': [wa_event('w1', phone='77055551044', from_me=True, ts=WA_TS + 7200, text='Ошиблась чатом'),
                                  wa_event('w2', phone='77055551044', from_me=True, ts=WA_TS + 7210, revoke_of='w1')]})
    with pytest.raises(HiringError) as caught:
        service.move(me, gone['id'], 'invited_office', office)
    assert caught.value.code == 'HIRING_NO_TOUCH'

    # Повторная активация: прежняя переписка не в счёт, нужна новая связь.
    with world['db']._get_cursor() as cur:
        no_contact = queries.reason_by_name(cur, 'refusal_candidate', rules.REASON_NO_CONTACT)
    _outcome, back = add_response(world, REC1, '8 705 555 10 33')
    service.wa_events({'events': [wa_event('v1', phone='77055551033', from_me=True, ts=WA_TS + 3700, text='Здравствуйте!')]})
    service.take(me, back['id'])
    service.move(me, back['id'], 'refused', {'initiator': 'candidate', 'reason_id': no_contact['id']})
    clock.now = NOW + timedelta(days=1)
    back = service.move(lead, back['id'], 'in_work', {'reactivate': True, 'recruiter_id': REC1})
    with pytest.raises(HiringError) as caught:
        service.move(me, back['id'], 'refused', {'initiator': 'candidate', 'reason_id': no_contact['id']})
    assert caught.value.code == 'HIRING_NO_TOUCH'
    service.wa_events({'events': [wa_event('v2', phone='77055551033', from_me=True, ts=WA_TS + 86400 + 600, text='Вы ещё ищете работу?')]})
    assert service.move(me, back['id'], 'refused', {'initiator': 'candidate', 'reason_id': no_contact['id']})['stage'] == 'refused'


def test_whatsapp_reply_clears_no_answer_and_pool_return_needs_new_contact(world):
    """Владелец 09.11.2026: «Недозвон / не ответил» — кандидат не ответил ни на звонок, ни на сообщение в WhatsApp.
    Ответ кандидата в WhatsApp статус снимает (и в плитке «Отчётов» его больше нет), наше сообщение — нет. После
    возврата в пул переписка прошлого закрепления новому рекрутеру связью не засчитывается; второй номер WhatsApp
    карточки — засчитывается."""
    service, clock = world['service'], world['clock']
    me, other, lead = ctx_of(world, REC1), ctx_of(world, REC2), ctx_of(world, LEAD_GRANT)
    office = {'office_visit_at': '2026-10-20T10:00'}
    filters = reports.parse_filters({'month': '2026-10'})

    # Карточка не из WhatsApp: недозвон, потом кандидат пишет на номер найма (чат ещё не привязан — путь повторного отклика).
    _outcome, card = add_response(world, REC1, '8 705 555 20 01')
    service.take(me, card['id'])
    clock.now = NOW + timedelta(minutes=5)
    card = service.touch(me, card['id'], {'kind': 'call', 'result': 'no_answer'})
    assert (card['substage'], card['no_answer_streak']) == ('no_answer', 1)
    # Сообщение, написанное ДО нашего звонка (шлюз дослал после простоя), — не ответ на звонок.
    service.wa_events({'events': [wa_event('n1', phone='77055552001', ts=WA_TS + 60, text='Позвоните мне')]})
    with world['db']._get_cursor() as cur:
        assert queries.get_card(cur, card['id'])['substage'] == 'no_answer'
        assert reports.card_ids_for(cur, filters, 'total:no_answer') == [card['id']]
    service.wa_events({'events': [wa_event('n2', phone='77055552001', ts=WA_TS + 600, text='Я на работе, пишите сюда')]})
    with world['db']._get_cursor() as cur:
        fresh = queries.get_card(cur, card['id'])
        assert (fresh['substage'], fresh['no_answer_streak']) == ('whatsapp', 0)
        assert reports.card_ids_for(cur, filters, 'total:no_answer') == []
    # Снова недозвон, и мы пишем сами с телефона найма — статус остаётся, пока кандидат молчит.
    clock.now = NOW + timedelta(minutes=20)
    service.touch(me, card['id'], {'kind': 'call', 'result': 'no_answer'})
    service.wa_events({'events': [wa_event('n3', phone='77055552001', from_me=True, ts=WA_TS + 1800, text='Перезвоните, пожалуйста')]})
    with world['db']._get_cursor() as cur:
        assert queries.get_card(cur, card['id'])['substage'] == 'no_answer'
        assert reports.card_ids_for(cur, filters, 'total:no_answer') == [card['id']]

    # Возврат в пул: переписка прошлого закрепления новому рекрутеру не засчитывается — как и чужие звонки.
    _outcome, back = add_response(world, REC1, '8 705 555 20 02')
    service.take(me, back['id'])
    service.wa_events({'events': [wa_event('p1', phone='77055552002', from_me=True, ts=WA_TS + 3600, text='Здравствуйте!')]})
    clock.now = NOW + timedelta(days=2)
    service.reassign(lead, back['id'], None, 'Рекрутер в отпуске')
    service.take(other, back['id'])
    with pytest.raises(HiringError) as caught:
        service.move(other, back['id'], 'invited_office', office)
    assert caught.value.code == 'HIRING_NO_TOUCH'
    service.wa_events({'events': [wa_event('p2', phone='77055552002', from_me=True, ts=WA_TS + 2 * 86400 + 600,
                                           text='Добрый день! Вы ещё ищете работу?')]})
    assert service.move(other, back['id'], 'invited_office', office)['stage'] == 'invited_office'

    # Второй номер WhatsApp карточки: наше сообщение только в его чате — тоже связь.
    _outcome, twin = add_response(world, REC1, '8 705 555 20 03')
    service.take(me, twin['id'])
    service.edit_card(me, twin['id'], {'whatsapp': '8 705 555 20 04'})
    with pytest.raises(HiringError) as caught:
        service.move(me, twin['id'], 'invited_office', office)
    assert caught.value.code == 'HIRING_NO_TOUCH'
    service.wa_events({'events': [wa_event('t1', phone='77055552004', from_me=True, ts=WA_TS + 2 * 86400 + 900, text='Пишу сюда, как просили')]})
    assert service.move(me, twin['id'], 'invited_office', office)['stage'] == 'invited_office'


class FakeStorage:
    def __init__(self):
        self.files, self.deleted, self.signed = {}, [], []

    def available(self):
        return True

    def upload(self, path, data, ctype):
        self.files[path] = (data, ctype)

    def download(self, path):
        return self.files[path][0]

    def delete(self, path):
        self.deleted.append(path)
        self.files.pop(path, None)

    def sign(self, path, ctype, disposition):
        self.signed.append((path, ctype, disposition))
        return 'https://storage.test/' + path


class VoiceRetry(Exception):
    kind = 'retry'


def media_world(world, results):
    """Сервис с бакетом и распознаванием-подделками; фоновая работа — сразу, в том же потоке."""
    storage, heard = FakeStorage(), []

    def transcriber(raw, ctype):
        heard.append((raw, ctype))
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    service = HiringService(cursor_factory=world['db']._get_cursor, now=world['clock'], storage=storage,
                            transcriber=transcriber, spawn=lambda target, *args: target(*args))
    return service, storage, heard


def test_whatsapp_media_files_and_transcripts(world):
    """Владелец 09.11.2026: файлы и голосовые кандидатов видны в карточке. Шлюз качает файл следом за
    сообщением: в бакет, голосовое — в расшифровку; удалённое у всех — стирается и из бакета."""
    ready = {'status': 'ready', 'text': 'Здравствуйте, я по вакансии', 'language': 'ru', 'model': 'gemini-3.8-flash',
             'cost': 0.0012, 'usage': {'big': 'blob'}, 'error': None}
    service, storage, heard = media_world(world, [ready])
    voice = {'mime': 'audio/ogg; codecs=opus', 'size': 4096, 'seconds': 7, 'fetch': True}
    service.wa_events({'events': [wa_event('v1', kind='voice', text='', media=voice)]})
    with world['db']._get_cursor() as cur:
        card = queries.open_card_by_phone(cur, '+77055552233')
    lead = ctx_of(world, HR_HEAD)
    assert service.card_whatsapp(lead, card['id'])['messages'][0]['media']['state'] == 'loading'
    # Файл пришёл: лёг в бакет, голосовое расшифровано (поток — сразу), в ленте — ссылка и текст.
    assert service.wa_media('v1', b'OggS-voice') == {'stored': True}
    (path, (data, ctype)), = storage.files.items()
    assert path.startswith('hiring/whatsapp/2026/10/') and path.endswith('.ogg') and data == b'OggS-voice' and ctype == 'audio/ogg'
    assert heard == [(b'OggS-voice', 'audio/ogg')]
    media = service.card_whatsapp(lead, card['id'])['messages'][0]['media']
    assert media['state'] == 'stored' and media['url'] == 'https://storage.test/' + path and media['inline'] is True
    assert media['transcript'] == {'status': 'ready', 'text': 'Здравствуйте, я по вакансии'} and 'object' not in media
    with world['db']._get_cursor() as cur:
        stored = queries.wa_messages_by_id(cur, 'v1')[0]['media']
    assert stored['transcript']['model'] == 'gemini-3.8-flash' and 'usage' not in stored['transcript']
    # Повтор файла — второй копии нет; чужой id, пустой и слишком большой файл — отказ.
    assert service.wa_media('v1', b'OggS-voice') == {'stored': False, 'reason': 'already'} and len(storage.files) == 1
    for wa_id, data, status in (('nope', b'x', 404), ('../x', b'x', 400), ('v1', b'', 400),
                                ('v1', b'x' * (wa_media.MAX_BYTES + 1), 413)):
        with pytest.raises(HiringError) as refused:
            service.wa_media(wa_id, data)
        assert refused.value.status == status
    with pytest.raises(HiringError) as no_storage:
        world['service'].wa_media('v1', b'x')
    assert no_storage.value.status == 503
    # Документ — скачиванием со своим именем; файл, которого у WhatsApp нет, — «не сохранился».
    service.wa_events({'events': [
        wa_event('d1', kind='document', text='резюме', ts=WA_TS + 60,
                 media={'mime': 'application/pdf', 'size': 2048, 'file_name': 'Резюме.pdf', 'fetch': True}),
        wa_event('p1', kind='image', text='', ts=WA_TS + 120, media={'mime': 'image/jpeg', 'size': 900, 'fetch': True}),
        wa_event('big', kind='video', text='', ts=WA_TS + 180, media={'mime': 'video/mp4', 'size': wa_media.MAX_BYTES + 5}),
        wa_event('old', kind='image', text='', ts=WA_TS + 240, media={'mime': 'image/jpeg', 'size': 900}),
    ]})
    assert service.wa_media('d1', b'%PDF-1.7') == {'stored': True}
    assert service.wa_events({'events': [{'type': 'media_failed', 'id': 'p1', 'reason': 'gone'}]})['outcomes'] == {'media_failed': 1}
    thread = {m['wa_id']: m['media'] for m in service.card_whatsapp(lead, card['id'])['messages']}
    assert thread['d1']['state'] == 'stored' and thread['d1']['file_name'] == 'Резюме.pdf' and thread['d1']['inline'] is False
    assert any(sign[1] == 'application/pdf' and sign[2].startswith('attachment; filename="') for sign in storage.signed)
    assert thread['p1'] == {'mime': 'image/jpeg', 'size': 900, 'state': 'failed', 'reason': 'файла у WhatsApp уже нет'}
    # Старое сообщение без метки шлюза — его файл шлюз попросит у телефона заново: тоже «загружается…».
    assert thread['big']['state'] == 'too_large' and thread['old']['state'] == 'loading'
    # Кандидат удалил голосовое у всех — исчезает и из бакета; файл, пришедший после удаления, не кладётся.
    assert service.wa_events({'events': [wa_event('v2', revoke_of='v1')]})['outcomes'] == {'revoked': 1}
    assert storage.deleted == [path] and path not in storage.files
    assert service.wa_media('v1', b'OggS-voice') == {'stored': False, 'reason': 'skipped'}


def test_whatsapp_transcript_retries_then_gives_up(world):
    service, storage, heard = media_world(world, [VoiceRetry('Vertex не ответил за 50 с'), VoiceRetry('ещё раз'),
                                                  VoiceRetry('и ещё')])
    clock = world['clock']
    service.wa_events({'events': [wa_event('w1', kind='voice', text='', media={'mime': 'audio/ogg', 'size': 10, 'fetch': True})]})
    assert service.wa_media('w1', b'OggS') == {'stored': True}
    with world['db']._get_cursor() as cur:
        transcript = queries.wa_messages_by_id(cur, 'w1')[0]['media']['transcript']
    assert transcript['status'] == 'pending' and transcript['attempts'] == 1 and 'не ответил' in transcript['error']
    # Сторож раз в минуту берёт голосовые, чья пауза прошла; после трёх попыток — «failed», без повторов.
    service.wa_watchdog()
    assert len(heard) == 1
    clock.now = NOW + timedelta(minutes=3)
    service.wa_watchdog()
    clock.now = NOW + timedelta(minutes=6)
    service.wa_watchdog()
    with world['db']._get_cursor() as cur:
        transcript = queries.wa_messages_by_id(cur, 'w1')[0]['media']['transcript']
    assert len(heard) == 3 and transcript['status'] == 'failed' and transcript['attempts'] == 3
    clock.now = NOW + timedelta(minutes=30)
    service.wa_watchdog()
    assert len(heard) == 3


def test_whatsapp_old_files_requested_from_phone(world, monkeypatch):
    """Владелец 10.11.2026: «фото отображается просто как фото, и непонятно, как его посмотреть». Файлы
    сообщений до 09.11.2026 шлюз не качал: портал отдаёт их шлюзу в ответе на опрос, шлюз просит телефон
    прислать сообщение заново, файл приходит обычным путём. Три просьбы без ответа — «не сохранился»."""
    service, storage, _heard = media_world(world, [])
    clock = world['clock']
    lid = '205981234567890'
    in_lid = {'chat': lid + '@lid', 'lid': lid}
    # Файл «загружается» дольше суток — очередь шлюза потерялась: его тоже просим у телефона.
    service.wa_events({'events': [wa_event('stale', kind='image', text='', media={'mime': 'image/jpeg', 'size': 50, 'fetch': True},
                                           **in_lid)]})
    clock.now = NOW + timedelta(hours=25)
    ts = WA_TS + 25 * 3600
    service.wa_events({'events': [
        wa_event('old1', kind='image', text='Я увидел вакансию', ts=ts - 300, media={'mime': 'image/jpeg', 'size': 900}, **in_lid),
        wa_event('old2', kind='voice', text='', ts=ts - 200, from_me=True, media={'mime': 'audio/ogg', 'size': 900}, **in_lid),
        wa_event('fresh', kind='image', text='', ts=ts - 100, media={'mime': 'image/jpeg', 'size': 900, 'fetch': True}, **in_lid),
        wa_event('huge', kind='video', text='', ts=ts - 90, media={'mime': 'video/mp4', 'size': wa_media.MAX_BYTES + 1}, **in_lid),
        wa_event('gone', kind='image', text='', ts=ts - 80, media={'mime': 'image/jpeg', 'size': 900}, **in_lid),
        wa_event('note', text='Ещё актуально?', ts=ts - 70, **in_lid),
        wa_event('pin', kind='sticker', text='', ts=ts - 60, **in_lid),
    ]})
    service.wa_events({'events': [wa_event('gone-x', revoke_of='gone', ts=ts - 50, **in_lid)]})
    with world['db']._get_cursor() as cur:
        card = queries.open_card_by_phone(cur, '+77055552233')
    lead = ctx_of(world, HR_HEAD)

    def thread():
        return {m['wa_id']: m.get('media') for m in service.card_whatsapp(lead, card['id'])['messages']}

    def poll(**extra):
        return service.wa_poll(dict({'state': 'connected', 'detail': {'paired': True}, 'features': ['resend']}, **extra))

    # Старый сервис (без features) и сервис не на связи просьб не получают — попытки не тратятся.
    assert 'resend' not in poll(features=None)
    assert 'resend' not in poll(state='connecting')
    # Сначала свежие, по две за опрос; своё сообщение — с from_me (ключ запроса — «отправлено мной»).
    # Отсчёт истории — следующее сообщение того же чата (телефон присылает историю до него).
    assert poll()['resend'] == [
        {'id': 'old2', 'chat': lid + '@lid', 'from_me': True, 'anchor': {'id': 'fresh', 'from_me': False, 'ts': ts - 100}},
        {'id': 'old1', 'chat': lid + '@lid', 'from_me': False, 'anchor': {'id': 'old2', 'from_me': True, 'ts': ts - 200}}]
    assert poll()['resend'] == [
        {'id': 'stale', 'chat': lid + '@lid', 'from_me': False, 'anchor': {'id': 'old1', 'from_me': False, 'ts': ts - 300}}]
    assert 'resend' not in poll()                                    # пауза до следующей просьбы
    assert thread()['old1'] == {'mime': 'image/jpeg', 'size': 900, 'state': 'loading'}
    # Файл old1 пришёл (из истории чата — без события; запасной путь — сообщение заново: событие — дубль,
    # второй строки нет).
    again = wa_event('old1', kind='image', text='Я увидел вакансию', ts=ts - 300,
                     media={'mime': 'image/jpeg', 'size': 900, 'fetch': True}, **in_lid)
    assert service.wa_events({'events': [again]})['outcomes'] == {'duplicate': 1}
    assert service.wa_media('old1', b'\xff\xd8jpeg') == {'stored': True} and len(storage.files) == 1
    # Через полчаса без ответа — новая просьба; последняя — другим видом чата (номером).
    clock.now += timedelta(minutes=31)
    assert [item['chat'] for item in poll()['resend']] == [lid + '@lid', lid + '@lid']
    clock.now += timedelta(minutes=31)
    assert [item['chat'] for item in poll()['resend']] == [lid + '@lid', lid + '@lid']
    clock.now += timedelta(minutes=31)
    assert [(item['id'], item['chat']) for item in poll()['resend']] == [
        ('old2', '77055552233@s.whatsapp.net'), ('stale', '77055552233@s.whatsapp.net')]
    # Четыре просьбы без ответа — «не сохранился (телефон не прислал файл)», больше не просим.
    clock.now += timedelta(minutes=31)
    assert 'resend' not in poll()
    files = thread()
    assert files['old1']['state'] == 'stored' and files['old1']['url'].startswith('https://storage.test/hiring/whatsapp/')
    assert files['old2'] == {'mime': 'audio/ogg', 'size': 900, 'state': 'failed', 'reason': 'телефон не прислал файл'}
    assert files['stale']['state'] == 'failed' and files['fresh']['state'] == 'loading'
    assert files['huge']['state'] == 'too_large' and files['pin'] == {'state': 'none'}
    # Сбой выборки не роняет опрос: состояние и команды сервиса важнее докачки файлов.
    monkeypatch.setattr(queries, 'wa_media_resend_due', lambda *args: (_ for _ in ()).throw(RuntimeError('сбой')))
    assert poll() == {'command': None, 'next_poll_sec': 10}


def test_whatsapp_repeat_closed_and_employee(world):
    service = world['service']
    outcome, card = add_response(world, REC1, '8 705 555 33 44', full_name='Петров')
    card = service.take(ctx_of(world, REC1), card['id'])
    # Открытая карточка с тем же телефоном из другого источника — повторный отклик один раз на чат.
    assert service.wa_events({'events': [wa_event('r1', phone='77055553344')]})['outcomes'] == {'repeat': 1}
    assert service.wa_events({'events': [wa_event('r2', phone='77055553344')]})['outcomes'] == {'stored': 1}
    with world['db']._get_cursor() as cur:
        repeats = [t for t in queries.card_touches(cur, card['id']) if t['kind'] == rules.TOUCH_REPEAT]
        assert len(repeats) == 1 and 'WhatsApp' in repeats[0]['body']
        cur.execute("SELECT COUNT(*) FROM hiring_notifications WHERE kind = 'repeat_response' AND user_id = %s", (REC1,))
        assert cur.fetchone()[0] == 1
        assert queries.wa_get_chat(cur, '+77055553344')['card_id'] == card['id']
        # Карточку закрыли — следующее обращение в WhatsApp заводит новую с пометкой «повторное».
        cur.execute("UPDATE hiring_cards SET is_closed = TRUE, closed_at = %s WHERE id = %s", (NOW, card['id']))
    assert service.wa_events({'events': [wa_event('r3', phone='77055553344')]})['outcomes'] == {'created': 1}
    with world['db']._get_cursor() as cur:
        fresh = queries.open_card_by_phone(cur, '+77055553344')
        assert fresh['repeat_of_card_id'] == card['id'] and queries.wa_get_chat(cur, '+77055553344')['card_id'] == fresh['id']
    # Телефон действующего сотрудника — не отклик; переписка всё равно хранится.
    assert service.wa_events({'events': [wa_event('e1', phone='77779998877')]})['outcomes'] == {'employee': 1}
    with world['db']._get_cursor() as cur:
        assert queries.open_card_by_phone(cur, '+77779998877') is None
        assert queries.wa_get_chat(cur, '+77779998877')['card_id'] is None


def test_whatsapp_hidden_and_foreign_numbers(world):
    service = world['service']
    lid = '205981234567890'
    hidden = wa_event('h1', phone='', chat=lid + '@lid', lid=lid, text='Добрый день')
    foreign = wa_event('f1', phone='998901234567')
    assert service.wa_events({'events': [hidden, foreign]})['outcomes'] == {'stored': 2}
    with world['db']._get_cursor() as cur:
        assert queries.wa_unmatched(cur) == {'foreign': 1, 'hidden': 1}
        assert queries.wa_get_chat(cur, 'lid:' + lid)['card_id'] is None
    # Номер стал известен (сервис прислал и LID, и номер) — чат доклеивается вместе с перепиской и
    # становится откликом.
    known = wa_event('h2', phone='77055556677', chat=lid + '@lid', lid=lid, text='Я по вакансии')
    assert service.wa_events({'events': [known]})['outcomes'] == {'created': 1}
    with world['db']._get_cursor() as cur:
        assert queries.wa_get_chat(cur, 'lid:' + lid) is None
        chat = queries.wa_get_chat(cur, '+77055556677')
        assert chat['messages'] == 2 and chat['lid'] == lid and chat['card_id']
        assert [m['wa_id'] for m in queries.wa_messages(cur, ['+77055556677'])] == ['h1', 'h2']
        assert queries.wa_unmatched(cur) == {'foreign': 1, 'hidden': 0}
    # Источник «WhatsApp» выключен руководителем — переписка хранится, откликов нет.
    with world['db']._get_cursor() as cur:
        cur.execute("UPDATE hiring_sources SET is_active = FALSE WHERE code = 'whatsapp'")
    assert service.wa_events({'events': [wa_event('o1', phone='77055558899')]})['outcomes'] == {'stored': 1}


def test_whatsapp_edit_revoke_and_access(world):
    service = world['service']
    service.wa_events({'events': [wa_event('x1', text='Черновик')]})
    edited = service.wa_events({'events': [wa_event('x2', edit_of='x1', text='Исправил')]})
    revoked = service.wa_events({'events': [wa_event('x3', revoke_of='x1')]})
    assert edited['outcomes'] == {'edited': 1} and revoked['outcomes'] == {'revoked': 1}
    with world['db']._get_cursor() as cur:
        card = queries.open_card_by_phone(cur, '+77055552233')
    message = service.card_whatsapp(ctx_of(world, REC1), card['id'])['messages'][0]
    assert message['body'] is None and message['deleted_at'] and message['edited_at']
    # Удалённому рекрутеру переписка карточки пула закрыта, как и телефон; чужому — карточки нет.
    with pytest.raises(HiringError) as hidden:
        service.card_whatsapp(ctx_of(world, REMOTE), card['id'])
    assert hidden.value.status == 403 and hidden.value.code == 'HIRING_PHONE_HIDDEN'
    with pytest.raises(HiringError) as stranger:
        service.card_whatsapp(ctx_of(world, STRANGER), card['id'])
    assert stranger.value.status == 404


def test_whatsapp_poll_commands_alerts_and_watchdog(world):
    service, clock = world['service'], world['clock']
    lead = ctx_of(world, HR_HEAD)
    with world['db']._get_cursor() as cur:
        cur.execute("UPDATE users SET telegram_id = 2020 WHERE id = %s", (HR_HEAD,))
    # Сервис ещё ни разу не выходил на связь — команду ставить некуда.
    with pytest.raises(HiringError) as early:
        service.wa_command(lead, 'pair')
    assert early.value.status == 409
    assert service.wa_poll({'state': 'unpaired', 'detail': {'version': 'wa-bridge 1.0.0'}}) == {'command': None, 'next_poll_sec': 10}
    with pytest.raises(HiringError) as recruiter:
        service.wa_command(ctx_of(world, REC1), 'pair')
    assert recruiter.value.status == 403
    assert service.wa_command(lead, 'pair')['pending'] == 'pair'
    command = service.wa_poll({'state': 'unpaired', 'detail': {}})['command']
    assert command['action'] == 'pair'
    # Пока сервис не подтвердил номером команды — она держится (потерянный ответ не теряет «Подключить»).
    assert service.wa_poll({'state': 'pairing', 'detail': {'qr': '2@one'}}) == {'command': command, 'next_poll_sec': 2}
    shown = service.wa_status(lead)
    assert shown['state'] == 'pairing' and shown['qr'] == '2@one' and shown['actions']['pair'] is False
    assert service.wa_poll({'state': 'pairing', 'detail': {'qr': '2@one'}, 'ack': command['id']})['command'] is None
    assert service.wa_status(lead)['pending'] is None
    service.wa_poll({'state': 'connected', 'detail': {'paired': True, 'number': '77001112233',
                                                       'connected_since': '2026-10-15T12:00:00'}})
    # Остановка — тревога руководителям один раз, сколько бы опросов ни пришло; Telegram — у кого он есть.
    parked = {'state': 'parked', 'detail': {'paired': True, 'park': {'reason': 'temp_ban', 'code': 101,
                                                                      'since': '2026-10-15T12:01:00', 'until': '2026-10-16T12:01:00'}}}
    service.wa_poll(parked)
    service.wa_poll(parked)
    with world['db']._get_cursor() as cur:
        cur.execute("SELECT user_id, body, tone FROM hiring_notifications WHERE kind = 'whatsapp' ORDER BY user_id")
        rows = cur.fetchall()
    assert [row[0] for row in rows] == [SUPER, HR_HEAD, LEAD_GRANT]
    assert all('временно ограничил' in row[1] and row[2] == 'warning' for row in rows)
    assert [chat_id for chat_id, _text, _markup in world['sent']] == [2020]
    assert service.wa_status(lead)['reason'].startswith('WhatsApp временно ограничил номер')
    # «Возобновить» — человек; после подключения — «снова подключён» без Telegram.
    service.wa_command(lead, 'resume')
    resume = service.wa_poll(parked)['command']
    assert resume['action'] == 'resume'
    service.wa_poll({'state': 'connected', 'ack': resume['id'],
                     'detail': {'paired': True, 'connected_since': '2026-10-15T13:00:00',
                                'phone_seen_at': (NOW - timedelta(days=13)).isoformat()}})
    with world['db']._get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM hiring_notifications WHERE kind = 'whatsapp' AND title LIKE '%%снова%%'")
        assert cur.fetchone()[0] == 3
    assert len(world['sent']) == 1
    # Сторож: телефон 13 дней не в сети — тревога раз в сутки.
    assert service.wa_watchdog() == 'wa:phone:2026-10-15'
    service.wa_watchdog()
    assert len(world['sent']) == 2
    # Сервис замолчал дольше трёх минут — «не на связи», тревога один раз.
    clock.now = NOW + timedelta(minutes=5)
    assert service.wa_watchdog().startswith('wa:offline:')
    assert service.wa_watchdog() is None
    assert service.wa_status(lead)['state'] == 'offline' and len(world['sent']) == 3
    with pytest.raises(HiringError):
        service.wa_command(lead, 'logout')
