# -*- coding: utf-8 -*-
"""«Дисциплина»: расхождения с ТЗ из сверки перед сдачей 20.11.2026, закрытые кодом.

На настоящем Postgres (DISCIPLINE_TEST_PORT), по пункту ТЗ на тест:
* п. 19.5 — испытательный срок и контроль после акта — разные основания: акт во
  время испытательного срока заводит свой контроль, правила постановки по виду;
* п. 6.2 — плашка контроля «поверх остальных»: счёт за период под ней остаётся;
* п. 3, 14 — меру решением по акту и по особому контролю руководитель снимает;
  оператор видит применённые к нему меры решений;
* п. 12 — весть о денежной мере по акту и по контролю; «следующее нарушение —
  акт» не уходит тем, кого и жёлтый блок «Сводки» не держит;
* п. 14 — имя файла акта об отказе в журнале;
* п. 4.1, 11, 15 — «на контроле» людей, а не строк; оператор в строках отчётов
  и журнала (переход в карточку по ФИО); меры решений в «Сводке по операторам».

    DISCIPLINE_TEST_PORT=5432 python -m pytest tests/test_discipline_tz_final_postgres.py
"""
from datetime import datetime, timedelta

import psycopg2
import pytest

from discipline import queries, service
from tests.test_discipline_postgres import (  # noqa: F401 — фикстуры db и section_open_for_all
    HEAD_SZOV, NOW, OP_A, OP_B, PORT, SUPER, SV_A, SV_B, SZOV, TODAY, bell_titles, create, ctx_of, db,
    enriched, measure_id, section_open_for_all, upload)
from tests.test_discipline_routes_postgres import _create, client, h  # noqa: F401 — фикстура client

pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DISCIPLINE_TEST_PORT)')

FINE = 'Лишение переменной части (фиксированная сумма)'
PRE_ACT = 'следующее нарушение — акт'


def _probation(cur, operator, *, days_ago=20, days_left=20, actor=HEAD_SZOV):
    return service.create_control(cur, ctx_of(cur, actor), {
        'operator_id': operator, 'source': 'probation',
        'start_date': (TODAY - timedelta(days=days_ago)).isoformat(),
        'end_date': (TODAY + timedelta(days=days_left)).isoformat()}, now=NOW, today=TODAY)


def _approved_act(cur, *, amount='5000', approver=SUPER):
    """Три нарушения — акт; руководитель утверждает его с денежным решением."""
    ids = [create(cur, SV_A, OP_A, NOW - timedelta(days=days)) for days in (12, 10, 8)]
    act = queries.acts_by_record(cur, [ids[-1]])[ids[-1]]
    record = service.load_record(cur, ctx_of(cur, approver), ids[-1], TODAY, for_update=True)
    assert service.update_act(cur, ctx_of(cur, approver), act, record, {
        'composed_on': TODAY.isoformat(), 'decision_measure_id': measure_id(cur, SZOV, FINE),
        'decision_amount': amount, 'approve': True}, now=NOW)
    return ids, queries.get_act(cur, act['id'])


# ── П. 19.5: разные основания, общий список ─────────────────────────────────

def test_probation_and_section_control_are_separate_bases(db):
    with db.cursor() as cur:
        head = ctx_of(cur, HEAD_SZOV)
        probation = _probation(cur, OP_B)
        # Контроль решением руководителя рядом с испытательным сроком — другое основание.
        manual = service.create_control(cur, head, {
            'operator_id': OP_B, 'source': 'manual', 'reason': 'Грубость с водителем',
            'end_date': (TODAY + timedelta(days=10)).isoformat()}, now=NOW, today=TODAY)
        actives = queries.active_controls_list(cur, [OP_B])[OP_B]
        assert [c['id'] for c in actives] == [manual, probation]
        assert queries.active_controls(cur, [OP_B])[OP_B]['id'] == manual
        # Но двух одного вида — нет.
        for source, text in (('manual', 'уже на особом контроле'), ('probation', 'Испытательный срок')):
            with pytest.raises(service.DisciplineError) as error:
                service.create_control(cur, head, {
                    'operator_id': OP_B, 'source': source, 'reason': 'Ещё раз',
                    'end_date': (TODAY + timedelta(days=5)).isoformat()}, now=NOW, today=TODAY)
            assert error.value.code == 'CONTROL_EXISTS' and text in error.value.message
        # Базу держит индекс: второй действующий испытательный срок не вставить и в обход сервиса.
        cur.execute('SAVEPOINT twin')
        with pytest.raises(psycopg2.errors.UniqueViolation):
            queries.insert_control(cur, {'operator_id': OP_B, 'department_id': SZOV, 'source': 'probation',
                                         'reason': 'Двойник', 'start_date': TODAY, 'end_date': TODAY})
        cur.execute('ROLLBACK TO SAVEPOINT twin')
        # Решённый контроль место освобождает.
        service.decide_control(cur, head, probation, {'decision': 'removed'}, now=NOW, today=TODAY)
        assert _probation(cur, OP_B)


def test_violations_count_in_every_control_whose_period_they_fall_in(db):
    """Нарушение в дни испытательного срока — нарушение в его период, даже если запись
    легла в контроль после акта; досрочно снятый контроль не считает нарушений после
    своего решения; запись-основание акта — не нарушение в период его контроля."""
    with db.cursor() as cur:
        head = ctx_of(cur, HEAD_SZOV)
        probation = _probation(cur, OP_A)
        remark = measure_id(cur, SZOV, 'Устное замечание')
        for days in (6, 4, 2):
            create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=days), measure_id=remark)
        after_act = queries.active_controls(cur, [OP_A])[OP_A]['id']
        create(cur, HEAD_SZOV, OP_A, NOW - timedelta(hours=3), measure_id=remark)

        def counts():
            rows = service.control_rows(cur, head, queries.list_controls(cur, [SZOV], state='all'), TODAY)
            return {row['id']: row['violations'] for row in rows}

        assert counts() == {probation: 4, after_act: 1}
        # Контроль после акта сняли в 12:00: нарушение в 11:00 было ещё в его период,
        # а в 12:00 и позже — уже только в период испытательного срока.
        service.decide_control(cur, head, after_act, {'decision': 'removed'}, now=NOW, today=TODAY)
        create(cur, HEAD_SZOV, OP_A, NOW - timedelta(hours=1), measure_id=remark)
        create(cur, HEAD_SZOV, OP_A, NOW, measure_id=remark)
        assert counts() == {probation: 6, after_act: 2}


def test_violation_under_both_controls_goes_to_the_section_one(db):
    """Новое нарушение при двух контролях ложится в контроль раздела (после акта),
    а задним числом, раньше его начала, — в испытательный срок."""
    with db.cursor() as cur:
        probation = _probation(cur, OP_A)
        remark = measure_id(cur, SZOV, 'Устное замечание')
        ids = [create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=days), measure_id=remark) for days in (6, 4, 2)]
        after_act = queries.active_controls(cur, [OP_A])[OP_A]
        assert after_act['source'] == 'act' and after_act['start_date'] == (NOW - timedelta(days=2)).date()
        assert queries.get_record(cur, ids[2], TODAY)['control_id'] == probation
        late = create(cur, HEAD_SZOV, OP_A, NOW - timedelta(hours=1), measure_id=remark)
        early = create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=3), measure_id=remark)
        assert queries.get_record(cur, late, TODAY)['control_id'] == after_act['id']
        assert queries.get_record(cur, early, TODAY)['control_id'] == probation


# ── П. 6.2: плашка контроля поверх плашки счёта ─────────────────────────────

def test_control_plate_keeps_the_count_under_it(db):
    with db.cursor() as cur:
        _probation(cur, OP_A)
        remark = measure_id(cur, SZOV, 'Устное замечание')
        for days in (6, 4):
            create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=days), measure_id=remark)
        context = service.operator_context(cur, ctx_of(cur, SV_A), OP_A, NOW, TODAY)
        plate = context['plate']
        assert plate['tone'] == 'red' and plate['title'].startswith('Оператор на особом контроле до')
        assert context['measure_required'] is True
        assert plate['under']['title'].startswith('За период уже 2 нарушения')
        assert 'потребуется акт' in plate['under']['text']


# ── П. 3, 14: мера решением по акту и по контролю снимается ─────────────────

def test_head_removes_measure_of_an_approved_act(db):
    with db.cursor() as cur:
        ids, act = _approved_act(cur)
        # Утверждённый акт с денежной мерой — весть руководителю, как у меры записи (п. 12).
        assert any(t.startswith('Мера с финансовым эффектом: %s' % FINE) for t in bell_titles(cur, HEAD_SZOV))
        decisions = queries.decision_measures(cur, [SZOV], operator_id=OP_A)
        assert [(d['source'], d['measure_title']) for d in decisions] == [('act', FINE)]
        assert [m['basis'] for m in service.own_decision_measures(cur, ctx_of(cur, OP_A))] == [
            'Акт %s' % act['number']]

        def remove(actor, reason):
            record = service.load_record(cur, ctx_of(cur, actor), ids[-1], TODAY, for_update=True)
            current = queries.get_act(cur, act['id'], for_update=True)
            service.remove_act_measure(cur, ctx_of(cur, actor), current, record, reason, now=NOW)

        with pytest.raises(service.DisciplineError) as error:
            remove(SV_A, 'СВ снимает меру')
        assert error.value.status == 403
        with pytest.raises(service.DisciplineError) as error:
            remove(SUPER, 'нет')
        assert error.value.code == 'REASON'
        remove(SUPER, 'Ошибка в сумме, акт пересоставят')
        stored = queries.get_act(cur, act['id'])
        assert stored['decision_removed_reason'] == 'Ошибка в сумме, акт пересоставят'
        assert stored['decision_removed_by'] == SUPER and stored['approved_at'] is not None
        assert stored['decision_title'] == FINE  # акт — документ: решение в нём остаётся
        assert queries.decision_measures(cur, [SZOV], operator_id=OP_A) == []
        assert service.own_decision_measures(cur, ctx_of(cur, OP_A)) == []
        history = [row for row in queries.record_history(cur, ids[-1]) if row['action'] == 'measure_remove']
        assert history and history[0]['changes']['basis'] == 'Акт %s' % act['number']
        assert history[0]['changes']['amount'] == 5000
        assert any(t.startswith('Снята мера с финансовым эффектом') for t in bell_titles(cur, HEAD_SZOV))
        with pytest.raises(service.DisciplineError):
            remove(SUPER, 'Повторное снятие')


def test_head_removes_measure_applied_by_control_decision(db):
    with db.cursor() as cur:
        control_id = _probation(cur, OP_B, days_left=0)
        service.decide_control(cur, ctx_of(cur, SUPER), control_id, {
            'decision': 'measure', 'measure_id': measure_id(cur, SZOV, FINE), 'measure_amount': '7000'},
            now=NOW, today=TODAY)
        assert any(t.startswith('Мера с финансовым эффектом: %s' % FINE) for t in bell_titles(cur, HEAD_SZOV))
        head = ctx_of(cur, HEAD_SZOV)

        def row():
            controls = queries.list_controls(cur, [SZOV], state='all')
            return next(r for r in service.control_rows(cur, head, controls, TODAY) if r['id'] == control_id)

        cur.execute("SELECT control_id FROM discipline_notifications WHERE kind = 'measure_finance'")
        assert {r[0] for r in cur.fetchall()} == {control_id}  # весть ведёт к самому контролю
        assert row()['can_remove_measure'] is True
        assert row()['can_remove_measure'] is not service.control_rows(
            cur, ctx_of(cur, SV_B), queries.list_controls(cur, [SZOV], state='all'), TODAY)[0]['can_remove_measure']
        with pytest.raises(service.DisciplineError) as error:
            service.remove_control_measure(cur, ctx_of(cur, SV_B), control_id, 'СВ снимает', now=NOW)
        assert error.value.status == 403
        service.remove_control_measure(cur, head, control_id, 'Приказ отменён', now=NOW)
        assert queries.decision_measures(cur, [SZOV], operator_id=OP_B) == []
        assert row()['can_remove_measure'] is False
        assert row()['decision_detail'].endswith('(снята 20.10.2026: Приказ отменён)')
        cur.execute("SELECT changes FROM discipline_audit WHERE action = 'measure_remove' AND entity = 'control'")
        assert cur.fetchone()[0]['basis'] == 'Решение по особому контролю'
        with pytest.raises(service.DisciplineError):
            service.remove_control_measure(cur, head, control_id, 'Повторно снимаю', now=NOW)


# ── П. 12: «следующее нарушение — акт» — как жёлтый блок ────────────────────

def test_pre_act_bell_only_where_the_next_violation_is_an_act(db):
    """П. 12: «второе нарушение за период — руководителю группы». Весть уходит, когда
    следующее нарушение действительно потребует акта — и без контроля, и на
    испытательном сроке (там лестница идёт как есть)."""
    with db.cursor() as cur:
        for days in (6, 4):
            create(cur, HEAD_SZOV, OP_B, NOW - timedelta(days=days))
        assert sum(PRE_ACT in t for t in bell_titles(cur, SV_B)) == 1
        _probation(cur, OP_A)
        remark = measure_id(cur, SZOV, 'Устное замечание')
        for days in (6, 4):
            create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=days), measure_id=remark)
        assert sum(PRE_ACT in t for t in bell_titles(cur, SV_A)) == 1


def test_pre_act_bell_is_silent_under_the_after_act_control(db):
    """Под контролем после акта второго акта нет (ступень «акт» — объяснительная и
    мера), даже когда окно уже не видит основание акта: «следующее — акт» не уходит."""
    from discipline import catalog
    with db.cursor() as cur:
        queries.save_settings(cur, SZOV, catalog.normalize_settings({'control_days': 60}), HEAD_SZOV)
        for days in (45, 42, 40):
            create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=days))
        control = queries.active_controls(cur, [OP_A])[OP_A]
        assert control['source'] == 'act' and control['end_date'] == (NOW - timedelta(days=40)).date() + timedelta(
            days=60)
        remark = measure_id(cur, SZOV, 'Устное замечание')
        for days in (5, 1):
            create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=days), measure_id=remark)
        assert not any(PRE_ACT in t for t in bell_titles(cur, SV_A))


def test_pre_act_bell_skips_a_period_already_covered_by_an_act(db):
    with db.cursor() as cur:
        # Акт на третьем нарушении, контроль после акта сняли досрочно.
        for days in (40, 35, 12):
            create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=days))
        control = queries.active_controls(cur, [OP_A])[OP_A]
        service.decide_control(cur, ctx_of(cur, HEAD_SZOV), control['id'], {'decision': 'removed'},
                               now=NOW, today=TODAY)
        # В окне снова два нарушения, но следующее — в периоде акта: акта не будет.
        create(cur, HEAD_SZOV, OP_A, NOW - timedelta(hours=1))
        assert service.operator_context(cur, ctx_of(cur, SV_A), OP_A, NOW, TODAY)['step']['action'] != 'act'
        assert not any(PRE_ACT in t for t in bell_titles(cur, SV_A))


# ── П. 14: акт об отказе в журнале — с именем файла ─────────────────────────

def test_refusal_names_its_file_in_the_journal(db):
    with db.cursor() as cur:
        record_id = create(cur, SV_A, OP_A, NOW - timedelta(days=1))
        record = service.load_record(cur, ctx_of(cur, SV_A), record_id, TODAY, for_update=True)
        service.record_refusal(cur, ctx_of(cur, SV_A), record, upload('акт об отказе.pdf'), [SV_B, HEAD_SZOV],
                               now=NOW)
        cur.execute("SELECT changes FROM discipline_audit WHERE action = 'refusal' AND record_id = %s", (record_id,))
        changes = cur.fetchone()[0]
        assert changes['file'] == 'акт об отказе.pdf' and len(changes['sha256']) == 64


# ── Ручки: «на контроле» людей, оператор в отчётах, снятие мер, экран оператора ──

def _route_act(client, db, *, approve_by=SUPER):
    now = datetime.now().replace(second=0, microsecond=0)
    created = [_create(client, SV_A, OP_A, now - timedelta(days=days)) for days in (4, 3, 2)]
    assert all(r.status_code == 201 for r in created), [r.get_json() for r in created]
    trigger = created[-1].get_json()['id']
    act = client.get('/api/discipline/records/%d' % trigger, headers=h(HEAD_SZOV)).get_json()['act_full']
    with db.cursor() as cur:
        fine = measure_id(cur, SZOV, FINE)
    response = client.patch('/api/discipline/acts/%d' % act['id'], headers=h(approve_by), json={
        'composed_on': now.date().isoformat(), 'decision_measure_id': fine, 'decision_amount': '5000',
        'approve': True})
    assert response.status_code == 200, response.get_json()
    return trigger, act['id']


def test_summary_counts_people_on_control_not_rows(client, db):
    now = datetime.now().replace(second=0, microsecond=0)
    assert client.post('/api/discipline/controls', headers=h(HEAD_SZOV), json={
        'operator_id': OP_A, 'source': 'probation', 'start_date': (now - timedelta(days=10)).date().isoformat(),
        'end_date': (now + timedelta(days=50)).date().isoformat()}).status_code == 201
    with db.cursor() as cur:
        remark = measure_id(cur, SZOV, 'Устное замечание')
    for days in (3, 2, 1):
        assert _create(client, SV_A, OP_A, now - timedelta(days=days), measure_id=str(remark)).status_code == 201
    summary = client.get('/api/discipline/summary', headers=h(HEAD_SZOV)).get_json()
    assert sorted(row['source'] for row in summary['controls']) == ['act', 'probation']
    assert summary['counters']['on_control'] == 1


def test_reports_and_audit_carry_the_operator_for_the_card(client, db):
    trigger, _act_id = _route_act(client, db)
    period = '?date_from=%s' % (datetime.now().date() - timedelta(days=30)).isoformat()
    operators = client.get('/api/discipline/reports/operators' + period, headers=h(HEAD_SZOV)).get_json()
    row = next(r for r in operators['rows'] if r['operator_id'] == OP_A)
    # Мера решением по акту — в «Сводке по операторам», как во вкладке «Меры».
    assert FINE in row['measures']
    assert 'operator_id' not in [c['key'] for c in operators['columns']]
    measures = client.get('/api/discipline/reports/measures' + period, headers=h(HEAD_SZOV)).get_json()
    assert {r['operator_id'] for r in measures['rows']} == {OP_A}
    audit = client.get('/api/discipline/audit' + period, headers=h(HEAD_SZOV)).get_json()
    assert any(item['operator_id'] == OP_A and item['record_id'] == trigger for item in audit['items'])
    # Excel отчёта — без служебного id.
    xlsx = client.get('/api/discipline/reports/operators%s&format=xlsx' % period, headers=h(HEAD_SZOV))
    assert xlsx.status_code == 200


def test_act_measure_removal_route_and_operator_screen(client, db):
    trigger, act_id = _route_act(client, db)
    detail = client.get('/api/discipline/records/%d' % trigger, headers=h(HEAD_SZOV)).get_json()['act_full']
    assert detail['can_remove_measure'] is True and detail['decision_removed_at'] is None
    assert client.get('/api/discipline/records/%d' % trigger,
                      headers=h(SV_A)).get_json()['act_full']['can_remove_measure'] is False
    # Оператор видит применённую к нему меру решением (п. 3) — на своём экране и в своей карточке.
    mine = client.get('/api/discipline/records?date_from=2000-01-01', headers=h(OP_A)).get_json()
    assert [(m['measure_title'], m['measure_amount']) for m in mine['decision_measures']] == [(FINE, 5000.0)]
    card = client.get('/api/discipline/operators/%d' % OP_A, headers=h(OP_A)).get_json()
    assert any(m['measure_title'] == FINE for m in card['measures'])
    assert 'decision_measures' not in client.get('/api/discipline/records', headers=h(HEAD_SZOV)).get_json()

    refused = client.post('/api/discipline/acts/%d/measure/remove' % act_id, headers=h(SV_A),
                          json={'reason': 'СВ снимает меру'})
    assert refused.status_code == 403
    short = client.post('/api/discipline/acts/%d/measure/remove' % act_id, headers=h(HEAD_SZOV), json={'reason': 'x'})
    assert (short.status_code, short.get_json()['code']) == (400, 'REASON')
    done = client.post('/api/discipline/acts/%d/measure/remove' % act_id, headers=h(HEAD_SZOV),
                       json={'reason': 'Ошибка в сумме удержания'})
    assert done.status_code == 200, done.get_json()
    detail = client.get('/api/discipline/records/%d' % trigger, headers=h(HEAD_SZOV)).get_json()['act_full']
    assert detail['can_remove_measure'] is False and detail['decision_removed_reason'] == 'Ошибка в сумме удержания'
    assert detail['decision_removed_by_name']
    since = (datetime.now().date() - timedelta(days=30)).isoformat()
    measures = client.get('/api/discipline/reports/measures?date_from=%s' % since, headers=h(HEAD_SZOV)).get_json()
    assert measures['rows'] == []
    mine = client.get('/api/discipline/records?date_from=2000-01-01', headers=h(OP_A)).get_json()
    assert mine['decision_measures'] == []


def test_control_measure_removal_route(client, db):
    today = datetime.now().date()
    created = client.post('/api/discipline/controls', headers=h(HEAD_SZOV), json={
        'operator_id': OP_B, 'source': 'manual', 'reason': 'Решение руководителя',
        'start_date': (today - timedelta(days=5)).isoformat(), 'end_date': today.isoformat()})
    control_id = created.get_json()['id']
    with db.cursor() as cur:
        fine = measure_id(cur, SZOV, FINE)
    assert client.post('/api/discipline/controls/%d/decide' % control_id, headers=h(HEAD_SZOV), json={
        'decision': 'measure', 'measure_id': fine, 'measure_amount': '3000'}).status_code == 200
    rows = client.get('/api/discipline/controls?state=closed', headers=h(HEAD_SZOV)).get_json()['items']
    assert rows[0]['can_remove_measure'] is True
    assert client.post('/api/discipline/controls/%d/measure/remove' % control_id, headers=h(SV_B),
                       json={'reason': 'СВ снимает'}).status_code == 403
    done = client.post('/api/discipline/controls/%d/measure/remove' % control_id, headers=h(HEAD_SZOV),
                       json={'reason': 'Сотрудник исправился'})
    assert done.status_code == 200, done.get_json()
    row = client.get('/api/discipline/controls?state=closed', headers=h(HEAD_SZOV)).get_json()['items'][0]
    assert row['can_remove_measure'] is False and row['decision_measure_removed_reason'] == 'Сотрудник исправился'
    assert '(снята ' in row['decision_detail']
