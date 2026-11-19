# -*- coding: utf-8 -*-
"""«Дисциплина»: расхождения с ТЗ из сверки 23.10.2026, закрытые кодом.

Каждый тест — один пункт сверки, на настоящем Postgres (DISCIPLINE_TEST_PORT):
мера в чужой записи на контроле (критерий 10), снятие меры (п. 3, 14), возврат
первой версии акта (п. 13), плашка и жёлтый блок после досрочного снятия
контроля (п. 6.2, 4.1.3), журнал просмотра карточки переведённого и файлов
при фиксации (п. 14), СВ с нулём и досрочно снятые контроли в отчётах (п. 15),
фильтр журнала по линии (п. 4.2), «кто составил» у акта (п. 9).

    DISCIPLINE_TEST_PORT=55461 python -m pytest tests/test_discipline_tz_gaps_postgres.py
"""
import io
from datetime import datetime, timedelta

import pytest

from discipline import queries, service
from tests.test_discipline_postgres import (  # noqa: F401 — фикстуры db и section_open_for_all
    HEAD_SZOV, NOW, OP_A, OP_B, PORT, SUPER, SV_A, SV_B, SZOV, TODAY, bell_titles, create, ctx_of, db,
    enriched, measure_id, section_open_for_all, upload)
from tests.test_discipline_routes_postgres import PNG, _create, client, h  # noqa: F401 — фикстура client

pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DISCIPLINE_TEST_PORT)')

FINE = 'Лишение переменной части (фиксированная сумма)'


def _act_and_control(cur, actor=SV_A, operator=OP_A):
    """Три нарушения за окно — акт по третьему и контроль после акта (лестница ТЗ)."""
    ids = [create(cur, actor, operator, NOW - timedelta(days=days)) for days in (12, 10, 8)]
    act = queries.acts_by_record(cur, [ids[-1]])[ids[-1]]
    control = queries.active_controls(cur, [operator])[operator]
    return ids, act, control


def _load(cur, actor, record_id):
    return service.load_record(cur, ctx_of(cur, actor), record_id, TODAY, for_update=True)


# ── Критерий 10: мера в чужой записи ────────────────────────────────────────

def test_second_supervisor_does_not_change_colleagues_measure(db):
    with db.cursor() as cur:
        # Второй СВ той же группы.
        cur.execute("INSERT INTO group_supervisor_memberships (group_id, supervisor_id, start_date) "
                    "VALUES (10, %s, '2026-01-01')", (SV_B,))
        _act_and_control(cur)
        reprimand = measure_id(cur, SZOV, 'Выговор')
        fourth = create(cur, SV_A, OP_A, NOW - timedelta(hours=2), measure_id=reprimand)
        assert enriched(cur, SV_A, fourth)['permissions']['can_propose_measure'] is True
        assert enriched(cur, SV_B, fourth)['permissions']['can_propose_measure'] is False
        remark = measure_id(cur, SZOV, 'Устное замечание')
        with pytest.raises(service.DisciplineError) as error:
            service.set_measure(cur, ctx_of(cur, SV_B), _load(cur, SV_B, fourth), {'measure_id': remark}, now=NOW)
        assert error.value.status == 403
        # Автор своей записи предложенную меру по-прежнему меняет.
        service.set_measure(cur, ctx_of(cur, SV_A), _load(cur, SV_A, fourth), {'measure_id': remark}, now=NOW)
        assert enriched(cur, SV_A, fourth)['measure_title'] == 'Устное замечание'


# ── П. 3, 14: снять меру; прежняя сумма в журнале ───────────────────────────

def test_head_removes_approved_fine_with_reason_and_history(db):
    with db.cursor() as cur:
        _act_and_control(cur)
        fine = measure_id(cur, SZOV, FINE)
        fourth = create(cur, SV_A, OP_A, NOW - timedelta(hours=2), measure_id=fine, measure_amount='5000')
        head = ctx_of(cur, HEAD_SZOV)
        # Руководитель меняет сумму — журнал хранит и прежнюю.
        service.set_measure(cur, head, _load(cur, HEAD_SZOV, fourth),
                            {'measure_id': fine, 'measure_amount': '15000'}, now=NOW)
        cur.execute("SELECT changes FROM discipline_audit WHERE record_id = %s AND action = 'measure_approve' "
                    "ORDER BY id DESC LIMIT 1", (fourth,))
        assert cur.fetchone()[0]['amount'] == [5000, 15000]

        # Утверждённое удержание тихо не меняется, но снимается — громко.
        record = _load(cur, HEAD_SZOV, fourth)
        assert record['measure_state'] == 'approved'
        with pytest.raises(service.DisciplineError):
            service.set_measure(cur, head, record, {'measure_id': fine, 'measure_amount': '1'}, now=NOW)
        with pytest.raises(service.DisciplineError) as error:
            service.remove_measure(cur, ctx_of(cur, SV_A), _load(cur, SV_A, fourth), 'досрочно приказом', now=NOW)
        assert error.value.status == 403
        with pytest.raises(service.DisciplineError) as error:
            service.remove_measure(cur, head, record, 'нет', now=NOW)
        assert error.value.code == 'REASON'

        service.remove_measure(cur, ctx_of(cur, SUPER), record, 'Снято досрочно приказом', now=NOW)
        item = enriched(cur, HEAD_SZOV, fourth)
        assert item['measure_title'] is None and item['measure_state'] is None
        # Под контролем запись снова ждёт меру — закрыть её без меры нельзя.
        assert item['state'] != 'measure_applied' and 'measure' in [b['code'] for b in item['close_blockers']]
        history = [row for row in queries.record_history(cur, fourth) if row['action'] == 'measure_remove']
        assert history and history[0]['changes']['reason'] == 'Снято досрочно приказом'
        assert history[0]['changes']['amount'] == 15000 and history[0]['changes']['measure'] == FINE
        # Весть о снятом удержании — тем, кто получал весть об утверждении (кроме снявшего).
        assert any(title.startswith('Снята мера с финансовым эффектом') for title in bell_titles(cur, HEAD_SZOV))


def test_measure_is_removed_from_a_closed_record_too(db):
    with db.cursor() as cur:
        first = create(cur, SV_A, OP_A, NOW - timedelta(days=3))
        service.add_explanation(cur, ctx_of(cur, OP_A), service.load_record(cur, ctx_of(cur, OP_A), first, TODAY),
                                upload(), now=NOW)
        head = ctx_of(cur, HEAD_SZOV)
        service.set_measure(cur, head, _load(cur, HEAD_SZOV, first),
                            {'measure_id': measure_id(cur, SZOV, 'Выговор')}, now=NOW)
        service.close_record(cur, head, _load(cur, HEAD_SZOV, first), now=NOW)
        assert enriched(cur, HEAD_SZOV, first)['permissions']['can_remove_measure'] is True
        service.remove_measure(cur, head, _load(cur, HEAD_SZOV, first), 'Взыскание снято досрочно', now=NOW)
        item = enriched(cur, HEAD_SZOV, first)
        assert item['state'] == 'closed' and item['measure_title'] is None
        assert item['permissions']['can_remove_measure'] is False


# ── П. 13: недействительная вторая версия акта возвращает первую ───────────

def test_invalid_second_act_version_restores_the_first(db):
    with db.cursor() as cur:
        ids, act, _control = _act_and_control(cur)
        head = ctx_of(cur, HEAD_SZOV)
        trigger = _load(cur, HEAD_SZOV, ids[-1])
        first = service.add_act_file(cur, head, act, trigger, upload('акт.pdf'), now=NOW)
        second = service.add_act_file(cur, head, queries.get_act(cur, act['id']), trigger, upload('акт-2.pdf'),
                                      now=NOW)
        assert enriched(cur, HEAD_SZOV, ids[-1])['act']['has_file']
        service.invalidate_file(cur, head, queries.get_file(cur, second), trigger, 'Чужой акт', now=NOW)
        assert queries.get_file(cur, first)['superseded_at'] is None
        item = enriched(cur, HEAD_SZOV, ids[-1])
        assert item['act']['has_file'] and 'act' not in [b['code'] for b in item['close_blockers']]
        cur.execute("SELECT entity_id FROM discipline_audit WHERE action = 'version_restored'")
        assert [row[0] for row in cur.fetchall()] == [first]
        # Первую тоже признали недействительной — прежней нет, запись снова ждёт акт.
        service.invalidate_file(cur, head, queries.get_file(cur, first), trigger, 'Без подписи', now=NOW)
        item = enriched(cur, HEAD_SZOV, ids[-1])
        assert not item['act']['has_file'] and 'act' in [b['code'] for b in item['close_blockers']]


# ── П. 6.2, 4.1.3: после досрочного снятия контроля ─────────────────────────

def test_plate_after_early_control_removal_matches_the_saved_record(db):
    with db.cursor() as cur:
        _ids, _act, control = _act_and_control(cur)
        service.decide_control(cur, ctx_of(cur, HEAD_SZOV), control['id'], {'decision': 'removed'},
                               now=NOW, today=TODAY)
        context = service.operator_context(cur, ctx_of(cur, SV_A), OP_A, NOW, TODAY)
        # Период уже покрыт актом: не «потребуется акт», а четвёртое — объяснительная.
        assert context['step']['action'] == 'explanation'
        assert context['plate']['tone'] == 'yellow' and 'акт' not in context['plate']['text']
        assert context['plate']['text'] == 'Это будет четвёртое — объяснительная №4.'
        fourth = create(cur, SV_A, OP_A, NOW - timedelta(hours=1))
        item = enriched(cur, SV_A, fourth)
        assert item['action'] == 'explanation' and item['act'] is None
        cur.execute('SELECT COUNT(*) FROM discipline_acts')
        assert cur.fetchone()[0] == 1


def test_summary_yellow_block_skips_period_already_covered_by_act(client, db):
    now = datetime.now().replace(second=0, microsecond=0)
    for days in (5, 4, 3):
        assert _create(client, SV_A, OP_A, now - timedelta(days=days)).status_code == 201
    control = next(c for c in client.get('/api/discipline/controls', headers=h(HEAD_SZOV)).get_json()['items']
                   if c['operator_id'] == OP_A)
    response = client.post('/api/discipline/controls/%d/decide' % control['id'], headers=h(HEAD_SZOV),
                           json={'decision': 'removed'})
    assert response.status_code == 200, response.get_json()
    summary = client.get('/api/discipline/summary', headers=h(HEAD_SZOV)).get_json()
    assert OP_A not in [row['operator_id'] for row in summary['pre_act']]
    context = client.get('/api/discipline/operator-context?operator_id=%d' % OP_A, headers=h(SV_A)).get_json()
    assert context['plate']['tone'] == 'yellow'
    card = client.get('/api/discipline/operators/%d' % OP_A, headers=h(HEAD_SZOV)).get_json()
    assert card['period']['next_title'].startswith('Объясн.') and 'акт' not in card['period']['next_title']


# ── П. 14: журнал действий ──────────────────────────────────────────────────

def test_card_view_of_person_moved_out_is_in_heads_journal(client, db):
    now = datetime.now().replace(second=0, microsecond=0)
    assert _create(client, SV_A, OP_A, now - timedelta(days=2)).status_code == 201
    with db.cursor() as cur:
        # Перевели в Удалённый КЦ — отдел вне раздела.
        cur.execute('UPDATE users SET department_id = 4 WHERE id = %s', (OP_A,))
        cur.execute('UPDATE group_operator_memberships SET end_date = %s WHERE operator_id = %s',
                    (now.date() - timedelta(days=1), OP_A))
    assert client.get('/api/discipline/operators/%d' % OP_A, headers=h(HEAD_SZOV)).status_code == 200
    journal = client.get('/api/discipline/audit', headers=h(HEAD_SZOV)).get_json()['items']
    assert any(row['action'] == 'view_card' and row['entity_id'] == OP_A for row in journal)


def test_evidence_attached_at_fixation_is_logged_file_by_file(client):
    now = datetime.now().replace(second=0, microsecond=0)
    response = _create(client, SV_A, OP_A, now - timedelta(days=1),
                       files=[(io.BytesIO(PNG), 'экран-1.png'), (io.BytesIO(PNG), 'экран-2.png')])
    assert response.status_code == 201, response.get_json()
    card = client.get('/api/discipline/records/%d' % response.get_json()['id'], headers=h(SV_A)).get_json()
    uploads = [row['changes'] for row in card['history'] if row['action'] == 'upload_evidence']
    assert len(uploads) == 2 and 'экран-1.png' in uploads[0] + uploads[1] and 'экран-2.png' in uploads[0] + uploads[1]


# ── П. 15: отчёты ───────────────────────────────────────────────────────────

def test_supervisors_report_lists_supervisor_with_zero_records(client):
    now = datetime.now().replace(second=0, microsecond=0)
    assert _create(client, SV_A, OP_A, now - timedelta(days=1)).status_code == 201
    period = {'date_from': (now - timedelta(days=30)).strftime('%Y-%m-%d'), 'date_to': now.strftime('%Y-%m-%d')}
    rows = client.get('/api/discipline/reports/supervisors', headers=h(HEAD_SZOV),
                      query_string=period).get_json()['rows']
    totals = {row['name']: row['total'] for row in rows}
    assert totals['Супервайзер Альфа'] == 1 and totals['Супервайзер Бета'] == 0


# ── П. 4.2: фильтр журнала по линии ─────────────────────────────────────────

def test_journal_filters_by_line(client, db):
    with db.cursor() as cur:
        cur.execute("INSERT INTO directions (id, name) VALUES (8, 'Чат водителей')")
        cur.execute('UPDATE groups SET direction_id = 8 WHERE id = 11')
    now = datetime.now().replace(second=0, microsecond=0)
    assert _create(client, HEAD_SZOV, OP_A, now - timedelta(days=2)).status_code == 201
    assert _create(client, HEAD_SZOV, OP_B, now - timedelta(days=1)).status_code == 201
    journal = client.get('/api/discipline/records', headers=h(HEAD_SZOV)).get_json()
    assert journal['directions'] == ['Линия водителей', 'Чат водителей']
    chat = client.get('/api/discipline/records', headers=h(HEAD_SZOV),
                      query_string={'direction': 'Чат водителей'}).get_json()
    assert [item['operator_id'] for item in chat['items']] == [OP_B]
    # Выбранная линия не сужает сам список линий — можно переключиться на другую.
    assert chat['directions'] == ['Линия водителей', 'Чат водителей']


# ── П. 9: кто составил акт ──────────────────────────────────────────────────

def test_act_shows_who_composed_it(client):
    now = datetime.now().replace(second=0, microsecond=0)
    ids = [_create(client, SV_A, OP_A, now - timedelta(days=days)).get_json()['id'] for days in (5, 4, 3)]
    act = client.get('/api/discipline/records/%d' % ids[-1], headers=h(HEAD_SZOV)).get_json()['act_full']
    response = client.patch('/api/discipline/acts/%d' % act['id'], headers=h(HEAD_SZOV),
                            json={'composed_on': now.strftime('%Y-%m-%d')})
    assert response.status_code == 200, response.get_json()
    act = client.get('/api/discipline/records/%d' % ids[-1], headers=h(HEAD_SZOV)).get_json()['act_full']
    # Составитель — СВ, зафиксировавший основание, как в черновике DOCX, а не глава, вписавший дату.
    assert act['composer_name'] == 'Супервайзер Альфа' and act['composed_by_name'] == 'Глава Сзовова'


def test_measure_remove_route(client):
    now = datetime.now().replace(second=0, microsecond=0)
    record_id = _create(client, SV_A, OP_A, now - timedelta(days=1)).get_json()['id']
    with_measure = client.get('/api/discipline/records/%d' % record_id, headers=h(HEAD_SZOV)).get_json()
    reprimand = next(m['id'] for m in with_measure['measures'] if m['title'] == 'Выговор')
    assert client.post('/api/discipline/records/%d/measure' % record_id, headers=h(HEAD_SZOV),
                       json={'measure_id': reprimand}).status_code == 200
    assert client.post('/api/discipline/records/%d/measure/remove' % record_id, headers=h(SV_A),
                       json={'reason': 'Снято досрочно'}).status_code == 403
    response = client.post('/api/discipline/records/%d/measure/remove' % record_id, headers=h(HEAD_SZOV),
                           json={'reason': 'Снято досрочно'})
    assert response.status_code == 200, response.get_json()
    record = client.get('/api/discipline/records/%d' % record_id, headers=h(HEAD_SZOV)).get_json()
    assert record['measure_title'] is None
    assert any(row['action'] == 'measure_remove' and 'Снято досрочно' in row['changes'] for row in record['history'])
