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

from hiring import notices, queries, reports, rules, schema
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
        assert [s['code'] for s in queries.list_sources(cur)][:4] == ['hh', 'kaspi', 'olx', 'target']
        assert {row['code'] for row in queries.list_integrations(cur)} == {'hh', 'olx', 'webhook'}
    ctx = ctx_of(world, HR_HEAD)
    assert ctx['headed_codes'] == ['hr'] and ctx['grant'] is None
    assert ctx_of(world, TRAINER)['trainer_group_ids'] == [world['group_id']]
    assert ctx_of(world, OP_HEAD)['vacancy_department_ids'] == [367]
    assert ctx_of(world, 999) is None


# ─── Приём и дубли (ТЗ 5.3) ────────────────────────────────────────────────────

def test_intake_dedupe_rules(world):
    service = world['service']
    outcome, card = add_response(world, REC1, '8 705 111 22 33', full_name='Иванов')
    assert outcome == 'created' and card['stage'] == 'responses' and card['phone'] == '+77051112233'
    outcome, same = add_response(world, REC2, '+7 705 111-22-33')
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
    card = service.touch(me, card['id'], {'kind': 'whatsapp'})
    assert card['substage'] == 'whatsapp' and card['no_answer_streak'] == 3
    card = service.touch(me, card['id'], {'kind': 'call', 'result': 'dial', 'next_contact_at': '2026-10-16T10:00'})
    assert card['substage'] == 'dial' and card['no_answer_streak'] == 0
    assert card['next_contact_at'] == datetime(2026, 10, 16, 10, 0)
    with pytest.raises(HiringError):
        service.touch(me, card['id'], {'kind': 'call'})
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
    # Смены отмечает руководитель отдела, не рекрутер и не руководитель рекрутинга.
    with pytest.raises(HiringError) as caught:
        service.mark_shift(me, card['id'], '2026-10-25')
    assert caught.value.code == 'HIRING_FORBIDDEN'
    with pytest.raises(HiringError) as caught:
        service.mark_shift(lead, card['id'], '2026-10-25')
    assert caught.value.code == 'HIRING_FORBIDDEN'
    clock.now = datetime(2026, 11, 3, 12, 0)
    for day in range(25, 31):
        card = service.mark_shift(head, card['id'], '2026-10-%02d' % day)
    assert card['shifts_done'] == 6
    with pytest.raises(HiringError) as caught:
        service.move(head, card['id'], 'hired', {})
    assert caught.value.code == 'HIRING_NEED_SHIFTS'
    with pytest.raises(HiringError) as caught:
        service.mark_shift(head, card['id'], '2026-11-04')
    assert caught.value.code == 'HIRING_FUTURE_SHIFT'
    card = service.mark_shift(head, card['id'], '2026-11-01')
    card = service.mark_shift(head, card['id'], '2026-11-01')   # повтор той же даты не удваивает
    assert card['shifts_done'] == 7
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
    add_response(world, REC1, '8 705 900 00 01')      # открытая карточка — строка станет повторным откликом
    raw = ('ФИО;Телефон;Город\nА;8 705 900 00 01;Алматы\nБ;8 705 900 00 02;Астана\nВ;8 705 900 00 02;Астана\n'
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
        assert total == 1 and rows[0]['phone'] == '+77059000002' and rows[0]['batch_id'] == batch['id']
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
                 VALUES ('itaxi', 't-old', '2026-10-01 10:00', '+77057000001', 'lead_created', '2026-10-01 10:00');
        """)
    assert service.pull_olx() == 0          # первый запуск ставит закладку, историю не льёт
    with world['db']._get_cursor() as cur:
        assert queries.get_integration(cur, 'olx')['cursor'] == {'journal_id': 1}
        cur.execute("""
            INSERT INTO olx_threads VALUES ('itaxi', 't-new', 'Ерлан', 'Водитель в iTaxi');
            INSERT INTO olx_journal (cabinet_code, thread_id, message_at, phone_normalized, result, created_at) VALUES
                ('itaxi', 't-new', '2026-10-15 11:00', '+77057000002', 'lead_created', '2026-10-15 11:00'),
                ('itaxi', 't-skip', '2026-10-15 11:05', NULL, 'canned_reply', '2026-10-15 11:05'),
                ('jana', 't-dup', '2026-10-15 11:10', '+77057000002', 'duplicate', '2026-10-15 11:10');
        """)
    assert service.pull_olx() == 2
    with world['db']._get_cursor() as cur:
        total, rows = queries.list_cards(cur, {'lead': True}, SUPER, {'source_id': world['sources']['olx']['id']})
        assert total == 1
        card = rows[0]
        assert card['full_name'] == 'Ерлан' and card['vacancy_text'] == 'Водитель в iTaxi'
        assert card['platform_ref'] == 'olx:itaxi:t-new' and card['received_at'] == datetime(2026, 10, 15, 11, 0)
        assert [t['kind'] for t in queries.card_touches(cur, card['id'])] == ['repeat_response']
        assert queries.get_integration(cur, 'olx')['cursor'] == {'journal_id': 4}


# ─── Дашборд (ТЗ 8.1) и рабочий стол (ТЗ 3) ───────────────────────────────────

def test_dashboard_cohort_and_click_through(world):
    service = world['service']
    me, trainer, head = ctx_of(world, REC1), ctx_of(world, TRAINER), ctx_of(world, OP_HEAD)
    hired = take_and_touch(world, REC1, '8 705 100 00 01', source_id=world['sources']['hh']['id'])
    service.move(me, hired['id'], 'invited_training', {'group_id': world['group_id']})
    service.move(trainer, hired['id'], 'on_training', {})
    service.move(me, hired['id'], 'on_line', {'line_started_at': '2026-10-20'})
    world['clock'].now = datetime(2026, 11, 2, 12, 0)
    for day in range(20, 27):
        service.mark_shift(head, hired['id'], '2026-10-%02d' % day)
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
