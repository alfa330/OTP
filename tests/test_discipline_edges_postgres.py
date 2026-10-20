# -*- coding: utf-8 -*-
"""«Дисциплина» на настоящем Postgres: крайние случаи лестницы, мер, файлов и
напоминаний — то, что ломалось после записи задним числом, правки, смены
настроек и признания файла недействительным.

    DISCIPLINE_TEST_PORT=55461 python -m pytest tests/test_discipline_edges_postgres.py
"""
from datetime import timedelta

import pytest

from discipline import access, catalog, queries, report, schema, service
from tests.test_discipline_postgres import (  # noqa: F401 — фикстура db
    DESCRIPTION, HEAD_SZOV, NOW, OP_A, OP_B, PORT, SUPER, SV_A, SV_B, SZOV, TODAY, _change_ladder,
    _create_at, bell_titles, create, ctx_of, db, enriched, measure_id, type_id, upload, use_sample_ladder,
)

pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DISCIPLINE_TEST_PORT)')


def _acts(cur):
    cur.execute('SELECT trigger_record_id FROM discipline_acts ORDER BY id')
    return [row[0] for row in cur.fetchall()]


def _unread(cur, user_id, kind):
    cur.execute('SELECT title, body FROM discipline_notifications WHERE user_id = %s AND kind = %s'
                ' AND read_at IS NULL ORDER BY id', (user_id, kind))
    return cur.fetchall()


# ── Один акт на период ───────────────────────────────────────────────────────

def test_backdated_record_inside_act_period_gives_no_second_act(db):
    with db.cursor() as cur:
        create(cur, SV_A, OP_A, NOW - timedelta(days=12))
        r2 = create(cur, SV_A, OP_A, NOW - timedelta(days=10))
        r3 = create(cur, SV_A, OP_A, NOW - timedelta(days=8))
        assert _acts(cur) == [r3]
        # Бумага пришла позже: нарушение раньше всех трёх — «третьей» стала r2.
        r0 = create(cur, SV_A, OP_A, NOW - timedelta(days=14))
        assert _acts(cur) == [r3]
        assert enriched(cur, HEAD_SZOV, r2)['action'] == 'explanation'
        assert enriched(cur, HEAD_SZOV, r3)['action'] == 'act'
        # В период акта вошло нарушение, которого в акте нет, — руководителю задача.
        number = queries.get_record(cur, r0, TODAY)['number']
        basis = _unread(cur, HEAD_SZOV, 'act_basis')
        assert len(basis) == 1 and number in basis[0][1]


def test_backdated_record_before_a_later_act_is_covered_by_it(db):
    """Запись задним числом встала раньше акта, но в его состав не входит."""
    with db.cursor() as cur:
        create(cur, SV_A, OP_A, NOW - timedelta(days=20))
        create(cur, SV_A, OP_A, NOW - timedelta(days=15))
        r4 = create(cur, SV_A, OP_A, NOW - timedelta(days=2))
        assert _acts(cur) == [r4]
        r3 = create(cur, SV_A, OP_A, NOW - timedelta(days=5))
        assert _acts(cur) == [r4]
        assert enriched(cur, HEAD_SZOV, r3)['action'] == 'explanation'


def test_act_placed_earlier_puts_later_records_under_control(db):
    with db.cursor() as cur:
        create(cur, SV_A, OP_A, NOW - timedelta(days=40))
        create(cur, SV_A, OP_A, NOW - timedelta(days=35))
        r4 = create(cur, SV_A, OP_A, NOW - timedelta(days=2))
        assert _acts(cur) == [] and queries.active_controls(cur, [OP_A]) == {}
        # Задним числом — третье в окне: акт теперь у неё, а более позднее
        # нарушение — уже в периоде контроля, ему нужна мера.
        r3 = create(cur, SV_A, OP_A, NOW - timedelta(days=30))
        assert _acts(cur) == [r3]
        control = queries.active_controls(cur, [OP_A])[OP_A]
        assert control['start_date'] == (NOW - timedelta(days=30)).date()
        item = enriched(cur, HEAD_SZOV, r4)
        assert item['under_control'] and item['action'] == 'explanation'
        assert 'measure' in [b['code'] for b in item['close_blockers']]
        bodies = [body for _title, body in _unread(cur, HEAD_SZOV, 'under_control')]
        assert any('по более раннему нарушению' in body for body in bodies)


def test_heavy_backdated_record_creates_one_act(db):
    with db.cursor() as cur:
        heavy = type_id(cur, SZOV, 'Сон на рабочем месте')
        cur.execute('UPDATE discipline_types SET weight = 2 WHERE id = %s', (heavy,))
        r1 = create(cur, SV_A, OP_A, NOW - timedelta(days=10), title='Опоздание к началу смены')
        r2 = create(cur, SV_A, OP_A, NOW - timedelta(days=8), title='Опоздание к началу смены')
        create(cur, SV_A, OP_A, NOW - timedelta(days=12))     # вес 2: r1 — третья, r2 — четвёртая
        assert _acts(cur) == [r1]
        assert queries.get_record(cur, r2, TODAY)['control_id'] is not None
        assert len([t for t, _b in _unread(cur, HEAD_SZOV, 'act') if 'нужен акт' in t]) == 1


def test_edit_that_raises_a_later_record_to_act(db):
    with db.cursor() as cur:
        r1 = create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=40))
        create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=12))
        r3 = create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=10))
        assert _acts(cur) == []
        head = ctx_of(cur, HEAD_SZOV)
        record = service.load_record(cur, head, r1, TODAY, for_update=True)
        service.edit_record(cur, head, record, {'occurred_at': (NOW - timedelta(days=15)).strftime('%Y-%m-%d %H:%M')},
                            now=NOW)
        assert _acts(cur) == [r3]
        assert queries.active_controls(cur, [OP_A])[OP_A]['source'] == 'act'


# ── Привязка акта к испытательному или ручному контролю ───────────────────────

def test_act_links_to_a_control_that_starts_later(db):
    with db.cursor() as cur:
        control_id = service.create_control(cur, ctx_of(cur, HEAD_SZOV), {
            'operator_id': OP_A, 'source': 'manual', 'reason': 'Решение руководителя',
            'start_date': (TODAY + timedelta(days=5)).isoformat(),
            'end_date': (TODAY + timedelta(days=20)).isoformat()}, now=NOW, today=TODAY)
        ids = [create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=d)) for d in (6, 4, 2)]
        control = queries.get_control(cur, control_id)
        # Начало сдвинуто на день акта: следующее нарушение — уже мера, не второй акт.
        assert control['act_id'] and control['start_date'] == (NOW - timedelta(days=2)).date()
        assert control['source'] == 'act'
        remark = measure_id(cur, SZOV, 'Устное замечание')
        fourth = create(cur, HEAD_SZOV, OP_A, NOW - timedelta(hours=1), measure_id=remark)
        assert enriched(cur, HEAD_SZOV, fourth)['under_control']
        assert _acts(cur) == [ids[2]]


# ── Утверждённое — окончательно ──────────────────────────────────────────────

def test_approved_act_decision_is_final(db):
    with db.cursor() as cur:
        ids = [create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=d)) for d in (12, 10, 8)]
        head = ctx_of(cur, HEAD_SZOV)
        act = queries.get_act(cur, enriched(cur, HEAD_SZOV, ids[2])['act']['id'])
        record = service.load_record(cur, head, ids[2], TODAY)
        fine = measure_id(cur, SZOV, 'Лишение переменной части (фиксированная сумма)')
        service.update_act(cur, head, act, record, {'composed_on': TODAY.isoformat(), 'decision_measure_id': fine,
                                                    'decision_amount': '20000', 'approve': True}, now=NOW)
        act = queries.get_act(cur, act['id'])
        with pytest.raises(service.DisciplineError) as error:
            service.update_act(cur, head, act, record, {'decision_measure_id': fine, 'decision_amount': '5000'},
                               now=NOW + timedelta(days=20))
        assert error.value.code == 'ACT_APPROVED'
        with pytest.raises(service.DisciplineError):
            service.update_act(cur, head, act, record, {'approve': True}, now=NOW + timedelta(days=20))
        assert queries.get_act(cur, act['id'])['approved_at'] == NOW


def test_approved_money_measure_is_locked_but_auto_applied_remark_is_fixable(db):
    with db.cursor() as cur:
        for days in (12, 10, 8):
            create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=days))
        fine = measure_id(cur, SZOV, 'Лишение переменной части (фиксированная сумма)')
        remark = measure_id(cur, SZOV, 'Устное замечание')
        written = measure_id(cur, SZOV, 'Письменное замечание')
        # СВ ошибся мерой без утверждения — сам и поправит.
        own = create(cur, SV_A, OP_A, NOW - timedelta(hours=3), measure_id=remark)
        record = service.load_record(cur, ctx_of(cur, SV_A), own, TODAY, for_update=True)
        service.set_measure(cur, ctx_of(cur, SV_A), record, {'measure_id': written}, now=NOW)
        assert enriched(cur, SV_A, own)['measure_state'] == 'proposed'
        # Утверждённое удержание не меняет никто.
        money = create(cur, HEAD_SZOV, OP_A, NOW - timedelta(hours=2), measure_id=fine, measure_amount='15000')
        record = service.load_record(cur, ctx_of(cur, HEAD_SZOV), money, TODAY, for_update=True)
        with pytest.raises(service.DisciplineError) as error:
            service.set_measure(cur, ctx_of(cur, HEAD_SZOV), record, {'measure_id': remark}, now=NOW)
        assert error.value.code == 'MEASURE_LOCKED'
        assert enriched(cur, HEAD_SZOV, money)['permissions']['can_propose_measure'] is False


def test_amount_with_non_breaking_space_is_accepted(db):
    with db.cursor() as cur:
        for days in (12, 10, 8):
            create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=days))
        fine = measure_id(cur, SZOV, 'Лишение переменной части (фиксированная сумма)')
        rid = create(cur, SV_A, OP_A, NOW - timedelta(hours=1), measure_id=fine, measure_amount='15 000')
        assert float(queries.get_record(cur, rid, TODAY)['measure_amount']) == 15000.0


def test_act_decision_of_cancelled_trigger_leaves_deductions(db):
    with db.cursor() as cur:
        ids = [create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=d)) for d in (12, 10, 8)]
        head = ctx_of(cur, HEAD_SZOV)
        act = queries.get_act(cur, enriched(cur, HEAD_SZOV, ids[2])['act']['id'])
        fine = measure_id(cur, SZOV, 'Лишение переменной части (фиксированная сумма)')
        service.update_act(cur, head, act, service.load_record(cur, head, ids[2], TODAY), {
            'composed_on': TODAY.isoformat(), 'decision_measure_id': fine, 'decision_amount': '20000',
            'approve': True}, now=NOW)
        window = dict(date_from=NOW - timedelta(days=1), date_to=NOW + timedelta(days=1))
        assert len(queries.decision_measures(cur, [SZOV], **window)) == 1
        service.cancel_record(cur, head, service.load_record(cur, head, ids[2], TODAY, for_update=True),
                              'Ошибочно зафиксировано', now=NOW)
        assert queries.decision_measures(cur, [SZOV], **window) == []


# ── Объяснительная ───────────────────────────────────────────────────────────

def test_late_request_gets_a_fresh_deadline(db):
    with db.cursor() as cur:
        use_sample_ladder(cur)
        late = create(cur, SV_A, OP_A, NOW - timedelta(days=10))
        cur.execute('UPDATE discipline_records SET created_at = %s WHERE id = %s', (NOW - timedelta(days=10), late))
        assert enriched(cur, SV_A, late)['action'] == 'training'
        create(cur, SV_A, OP_A, NOW - timedelta(days=12))        # раньше — тренинг стал объяснительной
        item = enriched(cur, SV_A, late)
        assert item['action'] == 'explanation'
        assert item['explanation_overdue'] is False and service._iso(item['explanation_due_at']) > NOW.isoformat()


def test_type_edit_turns_explanation_request_on_and_off(db):
    with db.cursor() as cur:
        loose = type_id(cur, SZOV, 'Опоздание к началу смены')
        cur.execute('UPDATE discipline_types SET needs_explanation = FALSE WHERE id = %s', (loose,))
        rid = create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=1), title='Опоздание к началу смены')
        assert not enriched(cur, HEAD_SZOV, rid)['needs_explanation_now']
        assert _unread(cur, OP_A, 'explanation_required') == []
        head = ctx_of(cur, HEAD_SZOV)
        strict = type_id(cur, SZOV, 'Сон на рабочем месте')
        service.edit_record(cur, head, service.load_record(cur, head, rid, TODAY, for_update=True),
                            {'type_id': strict}, now=NOW)
        assert len(_unread(cur, OP_A, 'explanation_required')) == 1
        service.edit_record(cur, head, service.load_record(cur, head, rid, TODAY, for_update=True),
                            {'type_id': loose}, now=NOW)
        assert _unread(cur, OP_A, 'explanation_required') == []


def test_invalidated_corrected_version_brings_back_the_previous_one(db):
    with db.cursor() as cur:
        rid = create(cur, SV_A, OP_A, NOW - timedelta(days=3))
        sv = ctx_of(cur, SV_A)
        service.add_explanation(cur, sv, service.load_record(cur, sv, rid, TODAY, for_update=True),
                                upload('first.pdf'), now=NOW)
        second = service.add_explanation(cur, sv, service.load_record(cur, sv, rid, TODAY, for_update=True),
                                         upload('second.pdf'), now=NOW)
        head = ctx_of(cur, HEAD_SZOV)
        service.invalidate_file(cur, head, queries.get_file(cur, second),
                                service.load_record(cur, head, rid, TODAY, for_update=True), 'Не тот файл', now=NOW)
        item = enriched(cur, HEAD_SZOV, rid)
        assert item['state'] == 'explanation_received'
        current = [f['file_name'] for f in queries.files_for(cur, record_ids=[rid])
                   if not f['invalid_at'] and not f['superseded_at']]
        assert current == ['first.pdf']


def test_invalidated_refusal_act_asks_for_explanation_again(db):
    with db.cursor() as cur:
        rid = create(cur, SV_A, OP_A, NOW - timedelta(days=3))
        sv = ctx_of(cur, SV_A)
        service.record_refusal(cur, sv, service.load_record(cur, sv, rid, TODAY, for_update=True), upload('refusal.pdf'),
                               [500, 501], now=NOW)
        refusal = [f for f in queries.files_for(cur, record_ids=[rid]) if f['kind'] == 'refusal_act'][0]
        head = ctx_of(cur, HEAD_SZOV)
        service.invalidate_file(cur, head, refusal, service.load_record(cur, head, rid, TODAY, for_update=True),
                                'Нет подписей свидетелей', now=NOW)
        record = queries.get_record(cur, rid, TODAY)
        assert record['refused_at'] is None and not record['refusal_witness_ids']
        assert enriched(cur, HEAD_SZOV, rid)['state'] == 'explanation_requested'


# ── «Проверить основание акта» ───────────────────────────────────────────────

def test_basis_task_is_single_and_goes_out_when_basis_returns(db):
    with db.cursor() as cur:
        ids = [create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=d)) for d in (12, 10, 8)]
        head = ctx_of(cur, HEAD_SZOV)

        def move(days):
            record = service.load_record(cur, head, ids[0], TODAY, for_update=True)
            service.edit_record(cur, head, record, {'occurred_at': (NOW - timedelta(days=days))
                                                    .strftime('%Y-%m-%d %H:%M')}, now=NOW)

        move(40)
        assert len(_unread(cur, HEAD_SZOV, 'act_basis')) == 1
        move(12)
        assert _unread(cur, HEAD_SZOV, 'act_basis') == []
        move(40)
        assert len(_unread(cur, HEAD_SZOV, 'act_basis')) == 1


# ── Правила счёта и повтор отправки ──────────────────────────────────────────

def test_record_in_the_same_second_as_ladder_change_keeps_old_rules(db):
    with db.cursor() as cur:
        use_sample_ladder(cur)
        create(cur, SV_A, OP_A, NOW - timedelta(days=12))
        create(cur, SV_A, OP_A, NOW - timedelta(days=10))
        _change_ladder(cur, catalog.LADDER_TZ, NOW)
        third = create(cur, SV_A, OP_A, NOW - timedelta(days=8))   # зафиксирована в ту же секунду
        item = enriched(cur, HEAD_SZOV, third)
        assert (item['action'], item['act']) == ('explanation', None)


def test_replay_needs_the_same_violation(db):
    with db.cursor() as cur:
        token = 'a1b2c3d4-e5f6-4789-9abc-def012345678'
        payload = {'operator_id': OP_A, 'type_id': type_id(cur, SZOV), 'description': DESCRIPTION,
                   'occurred_at': (NOW - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
                   'shift_label': '09:00–18:00', 'client_token': token}
        sv = ctx_of(cur, SV_A)
        first = service.create_record(cur, sv, dict(payload), [], now=NOW, today=TODAY)
        assert service.replayed_record(cur, sv, dict(payload))['id'] == first
        # Метка осталась в черновике, а вписали другое нарушение — это новая запись.
        other = dict(payload, occurred_at=(NOW - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'))
        assert service.replayed_record(cur, sv, other) is None
        second = service.create_record(cur, sv, other, [], now=NOW, today=TODAY)
        assert second != first and queries.get_record(cur, second, TODAY)['client_token'] is None


# ── Правка записи ────────────────────────────────────────────────────────────

def test_edit_needs_a_date_and_takes_the_group_of_the_new_day(db):
    with db.cursor() as cur:
        rid = create(cur, SV_A, OP_A, NOW - timedelta(days=2))
        cur.execute("UPDATE group_operator_memberships SET start_date = '2026-10-01' WHERE operator_id = %s", (OP_A,))
        cur.execute("INSERT INTO group_operator_memberships (group_id, operator_id, start_date, end_date) "
                    "VALUES (11, %s, '2026-09-01', '2026-09-30')", (OP_A,))
        sv = ctx_of(cur, SV_A)
        record = service.load_record(cur, sv, rid, TODAY, for_update=True)
        with pytest.raises(service.DisciplineError) as error:
            service.edit_record(cur, sv, record, {'occurred_at': ''}, now=NOW)
        assert error.value.code == 'OCCURRED_AT'
        service.edit_record(cur, sv, record, {'occurred_at': '2026-09-15 10:00'}, now=NOW)
        assert queries.get_record(cur, rid, TODAY)['group_name'] == 'Группа 2'


def test_second_supervisor_of_the_group_works_but_does_not_edit(db):
    """Решение автора (сказано владельцу): второй СВ группы ведёт запись коллеги —
    объяснительная, отказ, закрытие, — но не правит и не отменяет её (п. 3)."""
    with db.cursor() as cur:
        cur.execute("INSERT INTO group_supervisor_memberships (group_id, supervisor_id, start_date) "
                    "VALUES (10, %s, '2026-01-01')", (SV_B,))
        rid = create(cur, SV_A, OP_A, NOW - timedelta(days=3))
        item = enriched(cur, SV_B, rid)
        assert not item['permissions']['can_edit'] and not item['permissions']['can_cancel']
        assert item['permissions']['can_work'] and item['permissions']['can_close']


# ── Напоминания ──────────────────────────────────────────────────────────────

def test_control_in_a_department_without_head_reminds_super_admin(db):
    with db.cursor() as cur:
        cur.execute('UPDATE departments SET head_user_id = NULL WHERE id = %s', (SZOV,))
        service.create_control(cur, ctx_of(cur, SUPER), {
            'operator_id': OP_B, 'source': 'manual', 'reason': 'Решение руководителя',
            'end_date': TODAY.isoformat()}, now=NOW, today=TODAY)
        service.run_reminders(cur, now=NOW, today=TODAY)
        assert any('примите решение' in title for title in bell_titles(cur, SUPER))


# ── Схема ────────────────────────────────────────────────────────────────────

def test_schema_upgrade_from_the_first_version(db):
    """База, где раздел уже поднимался первой версией таблиц, получает новые
    колонки при следующем запуске — до индексов, которые на них опираются."""
    with db.cursor() as cur:
        cur.execute('DROP INDEX IF EXISTS ux_discipline_records_client_token')
        for table, columns in (
                ('discipline_records', ('client_token', 'explanation_requested_at')),
                ('discipline_acts', ('decision_finance', 'decision_value_kind', 'decision_amount',
                                     'decision_percent')),
                ('discipline_controls', ('decision_measure_id', 'decision_measure_title', 'decision_finance',
                                         'decision_value_kind', 'decision_amount', 'decision_percent'))):
            for column in columns:
                cur.execute('ALTER TABLE %s DROP COLUMN %s' % (table, column))
        schema.init_discipline_schema(cur)
        cur.execute("SELECT to_regclass('ux_discipline_records_client_token') IS NOT NULL")
        assert cur.fetchone()[0]
        rid = create(cur, SV_A, OP_A, NOW - timedelta(days=1), client_token='z9y8x7w6-0000-4000-8000-000000000001')
        assert queries.get_record(cur, rid, TODAY)['client_token']


def test_rules_mirror_for_measure_lock(db):
    # Чистое правило сверено с тем, что лежит в базе после утверждения.
    with db.cursor() as cur:
        for days in (12, 10, 8):
            create(cur, HEAD_SZOV, OP_A, NOW - timedelta(days=days))
        fine = measure_id(cur, SZOV, 'Лишение переменной части (фиксированная сумма)')
        rid = create(cur, HEAD_SZOV, OP_A, NOW - timedelta(hours=1), measure_id=fine, measure_amount='100')
        assert access.measure_locked(queries.get_record(cur, rid, TODAY))
        rows, _totals = report.report_measures([enriched(cur, HEAD_SZOV, rid)])
        assert rows[0]['measure_amount'] == 100.0
