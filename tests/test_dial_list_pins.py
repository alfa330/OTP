# -*- coding: utf-8 -*-
"""Закреплённые водители (владелец, 18.11.2026) без базы данных — то, что CI гоняет всегда.

Поведение на настоящем SQL — tests/test_dial_list_pins_postgres.py (нужен DIAL_LIST_TEST_PORT, в CI пропускается).
Здесь — проводка: одно правило закрепления стоит во всех местах, где решается «кому звонить», выдача и проверки
перед ней передают оператора, журнал не ставит закреплённого «на паузу», ручка проверяет тело запроса, схема без
блокировки users на старте, «Позвонить ещё раз» сверяет закрепление.
"""
import inspect
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import batch_focus  # noqa: E402
from dial_list import pins  # noqa: E402
from dial_list import routes as dial_routes  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

S = dial_service.DialListService
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PinSqlTests(unittest.TestCase):
    def test_conditions_are_safe_to_paste_anywhere(self):
        # Вставляются в тексты с %s (psycopg2), с именованными параметрами и в тексты, которые идут через str.format.
        for sql in (pins.pin_active_sql('l'), pins.pinned_sql('l'), pins.free_sql('l'), pins.department_pool_sql('l')):
            self.assertNotIn('%', sql)
            self.assertNotIn('{', sql)
            self.assertNotIn('}', sql)
        own = pins.pool_sql('l', '%s')
        self.assertEqual(own.count('%s'), 1)
        self.assertNotIn('{', own)
        self.assertEqual(pins.pin_active_sql('x').count('x.department_id'), 1)

    def test_never_null(self):
        # Журнал берёт NOT от условий, пул — OR: NULL съел бы водителя молча. Сравнение пары с NULL даёт NULL, поэтому
        # оно стоит только за проверкой IS [NOT] NULL, а в списке пар нет NULL — NOT IN всегда да или нет.
        active = pins.pin_active_sql('l')
        self.assertEqual(active, '(l.pinned_operator_id, l.department_id) IN (' + pins.active_members_sql() + ')')
        self.assertEqual(pins.pinned_sql('l'), '(l.pinned_operator_id IS NOT NULL AND ' + active + ')')
        self.assertEqual(pins.free_sql('l'), '(l.pinned_operator_id IS NULL OR NOT ' + active + ')')
        self.assertIn('WHERE pin_u.department_id IS NOT NULL AND', pins.active_members_sql())
        self.assertEqual(pins.department_pool_sql('l'),
                         '(' + pins.pinned_sql('l') + ' OR ' + batch_focus.in_work_sql('l') + ')')
        self.assertEqual(pins.pool_sql('l', '%s'), '(l.pinned_operator_id = %s OR (' + pins.free_sql('l') + ' AND '
                         + batch_focus.in_work_sql('l') + '))')

    def test_active_members_are_hashed_once_not_queried_per_driver(self):
        # Подзапрос на каждого водителя планировщик оценивал как ~30 тыс. подзапросов, оценка пула переходила порог
        # JIT, и компиляция добавляла 100–200 мс (замер на боевых данных 18.11.2026). Список пар не ссылается на
        # строку водителя — Postgres строит хэш один раз.
        active = pins.pin_active_sql('zz')
        self.assertEqual(active.count('zz.'), 2)          # только пара слева: (водитель, его отдел)
        self.assertTrue(active.startswith('(zz.pinned_operator_id, zz.department_id) IN (SELECT '))

    def test_pin_holds_only_while_the_operator_works_in_the_department(self):
        # То же правило, что у списка операторов отдела (_ACTIVE_MEMBER_SQL): не уволен, и либо сотрудник отдела не на
        # чужой линии, либо сидит на линии этого отдела.
        members = pins.active_members_sql()
        self.assertEqual(members.count("NOT IN ('fired', 'dismissal')"), 2)
        self.assertIn('SELECT pin_u.id, pin_u.department_id FROM users pin_u', members)
        self.assertIn('AND NOT EXISTS (SELECT 1 FROM dial_list_line_members pin_o WHERE pin_o.user_id = pin_u.id AND '
                      'pin_o.released_at IS NULL AND pin_o.department_id <> pin_u.department_id)', members)
        self.assertIn('UNION ALL SELECT pin_m.user_id, pin_m.department_id FROM dial_list_line_members pin_m', members)
        self.assertIn('WHERE pin_m.released_at IS NULL AND', members)
        member = S._ACTIVE_MEMBER_SQL
        self.assertIn('((u.department_id = %s AND (m.user_id IS NULL OR m.department_id = %s))', member)
        self.assertIn('OR m.department_id = %s)', member)
        # Статусы «не работает» — те же, что у панели SIP (database._SIP_INACTIVE_STATUSES; модуль не импортируем:
        # он поднимает базу при импорте).
        source = open(os.path.join(ROOT, 'database.py'), encoding='utf-8').read()
        found = re.search(r"_SIP_INACTIVE_STATUSES = \(([^)]*)\)", source)
        self.assertIsNotNone(found)
        self.assertEqual(tuple(s.strip().strip("'\"") for s in found.group(1).split(',') if s.strip()),
                         pins.INACTIVE_STATUSES)

    def test_pool_of_an_operator(self):
        pool = S._POOL_SQL
        self.assertIn('AND ' + pins.pool_sql('l', '%s'), pool)
        # Свои — первыми, дальше прежний порядок.
        self.assertIn('ORDER BY COALESCE(l.pinned_operator_id = %s, FALSE) DESC,\n'
                      '                 l.attempts_total ASC, l.last_attempt_at ASC NULLS FIRST, l.created_at ASC', pool)
        self.assertLess(pool.index(pins.pool_sql('l', '%s')), pool.index('ORDER BY'))
        self.assertIn(pins.pool_sql('l', '%s'), S._POOL_CANDIDATES_SQL)
        self.assertIn('ORDER BY COALESCE(l.pinned_operator_id = %s, FALSE) DESC', S._POOL_CANDIDATES_SQL)
        # Сводка отдела — без оператора.
        self.assertIn('AND ' + pins.department_pool_sql('l'), S._POOL_DEPARTMENT_COUNT_SQL)
        self.assertNotIn('pinned_operator_id = %s', S._POOL_DEPARTMENT_COUNT_SQL)

    def test_every_caller_passes_the_operator_twice(self):
        for name in ('get_state', 'issue_next_portion', '_precheck_signatures', '_precheck_trips'):
            src = inspect.getsource(getattr(S, name))
            self.assertIn('ctx["user_id"], ctx["user_id"]', src, name)
        summary = inspect.getsource(S.leads_summary)
        self.assertIn('cur.execute(self._POOL_DEPARTMENT_COUNT_SQL, (\n'
                      '                    department_id, active, settings["retry_after_hours"], settings["max_attempts"]))',
                      summary)
        # «База на паузе» в сводке: закреплённый в силе на паузе не стоит.
        self.assertIn('AND NOT """ + pins.department_pool_sql("l")', summary)

    def test_journal(self):
        sql = S._JOURNAL_BASE_SQL
        self.assertIn("WHEN NOT " + pins.department_pool_sql("l") + " THEN 'parked'", sql)
        self.assertIn('CASE WHEN ' + pins.pinned_sql('l') + ' THEN l.pinned_operator_id END AS pinned_operator_id', sql)
        self.assertIn('CASE WHEN ' + pins.pinned_sql('l') + ' THEN pnu.name END AS pinned_operator_name', sql)
        self.assertIn('LEFT JOIN users pnu ON pnu.id = l.pinned_operator_id', sql)
        columns = [c.strip() for c in S._JOURNAL_COLUMNS.split(',')]
        self.assertEqual(columns[62:], ['pinned_operator_id', 'pinned_operator_name'])
        row = [None] * 64
        row[0], row[1], row[3], row[29] = 'l1', 1954, '77010000001', 'queue'
        svc = S.__new__(S)
        svc.sign_checker = None
        self.assertIsNone(svc._journal_row(row, 3)["pinned_operator"])
        row[62], row[63] = 801, 'Фронтова Фарида'
        self.assertEqual(svc._journal_row(row, 3)["pinned_operator"], {"id": 801, "name": 'Фронтова Фарида'})

    def test_recall_checks_the_pin_before_taking_a_row(self):
        src = inspect.getsource(S.recall_lead)
        self.assertLess(src.index('self._pinned_to_other(cur, lead_id, ctx["user_id"])'),
                        src.index('self._recall_row(cur, ctx, lead_id)'))
        self.assertIn('raise DialListError(RECALL_PINNED_OTHER, 409)', src)
        self.assertEqual(dial_service.RECALL_PINNED_OTHER, 'Водитель закреплён за другим оператором')
        self.assertIn('l.pinned_operator_id <> %s AND " + pins.pinned_sql("l")',
                      inspect.getsource(S._pinned_to_other).replace('\n                    + ', ' + '))

    def test_schema_adds_columns_without_locking_users(self):
        ddl = [s for s in dial_schema.DDL if 'pinned_' in s]
        self.assertEqual(ddl, [
            'ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS pinned_operator_id INTEGER',
            'ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMP WITH TIME ZONE',
            'ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS pinned_by INTEGER',
        ])


class PinLeadsValidationTests(unittest.TestCase):
    def _svc(self, profile='remote_cc', configured=True):
        svc = S.__new__(S)
        svc.department_settings = lambda department_id: {"_period": None, "configured": configured, "profile": profile}

        class NoDb:
            def _get_cursor(self):
                raise AssertionError('до базы не доходим — запрос отклонён раньше')

        svc.db = NoDb()
        return svc

    def _refused(self, svc, *args, **kwargs):
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.pin_leads(*args, **kwargs)
        return str(ctx.exception), ctx.exception.status

    def test_body(self):
        svc = self._svc()
        self.assertEqual(self._refused(svc, 1954, 801, [], actor_id=1)[1], 400)
        self.assertEqual(self._refused(svc, 1954, 801, None, actor_id=1)[1], 400)
        self.assertEqual(self._refused(svc, 1954, 801, ['не-водитель'], actor_id=1)[1], 400)
        many = ['00000000-0000-4000-8000-%012d' % i for i in range(pins.PIN_BATCH_MAX + 1)]
        self.assertEqual(self._refused(svc, 1954, 801, many, actor_id=1), (
            f"lead_ids: не больше {pins.PIN_BATCH_MAX} водителей за раз", 400))
        # Ровно предел (и повторы схлопываются) — не отказ по числу: доходит до базы (здесь — до заглушки).
        with self.assertRaises(AssertionError):
            svc.pin_leads(1954, 801, many[:-1] + many[:3], actor_id=1)

    def test_only_remote_cc_and_only_connected_departments(self):
        lead = ['00000000-0000-4000-8000-000000000001']
        self.assertEqual(self._refused(self._svc(profile='tez'), 560, 530, lead, actor_id=1),
                         ("Закрепление водителей — только в удалённом КЦ", 409))
        self.assertEqual(self._refused(self._svc(configured=False), 77, 1, lead, actor_id=1)[1], 404)


class PinRouteTests(unittest.TestCase):
    def _client(self, svc):
        import flask
        requester = (5, 'Руководитель', 'x', 'admin', None, None, None, 'admin')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (5, requester, None), is_admin_role=lambda role: True,
            headed_department_ids=lambda rid: [], service=svc)
        app = flask.Flask('dial_list_pin_test')
        app.register_blueprint(bp)
        return app.test_client()

    def test_route_checks_the_body_and_passes_who(self):
        seen = []

        class Svc:
            def manager_scope(self, is_admin, heads, login=None, supervisor_department_code=None, **extra):
                return None

            def pin_leads(self, department_id, operator_id, lead_ids, actor_id=None, note=''):
                seen.append((department_id, operator_id, lead_ids, actor_id, note))
                return {"pinned": len(lead_ids)}

        client = self._client(Svc())
        url = '/api/dial_list/departments/1954/leads/pin'
        for bad in (['a'], {"operator_id": 801}, {"operator_id": 801, "lead_ids": 'x'},
                    {"operator_id": 'Фарида', "lead_ids": []}, {"operator_id": True, "lead_ids": []}):
            self.assertEqual(client.put(url, json=bad).status_code, 400, bad)
        self.assertEqual(client.put(url, data='не json', content_type='application/json').status_code, 400)
        self.assertEqual(seen, [])
        resp = client.put(url, json={"operator_id": '801', "lead_ids": ['l1', 'l2'], "note": 'список фронт-офиса'})
        self.assertEqual((resp.status_code, resp.get_json()), (200, {"status": "success", "pinned": 2}))
        self.assertEqual(seen[-1], (1954, 801, ['l1', 'l2'], 5, 'список фронт-офиса'))
        client.put(url, json={"operator_id": None, "lead_ids": ['l1']})
        self.assertEqual(seen[-1], (1954, None, ['l1'], 5, ''))
        self.assertEqual(client.get(url).status_code, 405)

    def test_route_refuses_someone_elses_department(self):
        seen = []

        class Svc:
            def manager_scope(self, is_admin, heads, login=None, supervisor_department_code=None, **extra):
                return [560]

            def pin_leads(self, *args, **kwargs):
                seen.append(args)
                return {}

        resp = self._client(Svc()).put('/api/dial_list/departments/1954/leads/pin',
                                       json={"operator_id": 801, "lead_ids": []})
        self.assertEqual((resp.status_code, resp.get_json()), (403, {"error": "Это не ваш отдел"}))
        self.assertEqual(seen, [])


if __name__ == '__main__':
    unittest.main()
