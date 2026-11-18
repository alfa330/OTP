# -*- coding: utf-8 -*-
"""Точечная обработка базы (владелец, 17.11.2026) без базы данных — то, что CI гоняет всегда.

Поведение на настоящем SQL — tests/test_dial_list_batch_focus_postgres.py (нужен DIAL_LIST_TEST_PORT, в CI
пропускается). Здесь — проводка: одно условие отметок стоит во всех местах, где решается «кому звонить», и не
стоит там, где обещанный звонок идёт мимо пула; журнал не называет «В очереди» тех, кого пул не выдаст; ручка
проверяет тело запроса; схема без блокировки users на старте.
"""
import inspect
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import batch_focus  # noqa: E402
from dial_list import routes as dial_routes  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

S = dial_service.DialListService
IN_WORK = batch_focus.in_work_sql("l")


class BatchFocusSqlTests(unittest.TestCase):
    def test_condition_is_safe_to_paste_anywhere(self):
        # Вставляется в тексты с %s (psycopg2) и в тексты, которые идут через str.format.
        self.assertNotIn('%', IN_WORK)
        self.assertNotIn('{', IN_WORK)
        self.assertNotIn('}', IN_WORK)
        self.assertEqual(batch_focus.in_work_sql("x").count("x.department_id"), 1)
        # Пары (файл, водитель) — только именованные параметры отдела и месяца, без фигурных скобок.
        members = batch_focus.file_members_sql()
        self.assertNotIn('{', members)
        self.assertEqual(members.count('%('), members.count('%(department_id)s') + members.count('%(period)s'))

    def test_condition_is_never_null_and_uses_hashed_subqueries(self):
        # Водитель без файла при отметках — FALSE, а не NULL: журнал берёт NOT от условия.
        self.assertEqual(IN_WORK.count('COALESCE('), 2)
        self.assertIn('l.first_batch_id IN (SELECT', IN_WORK)
        self.assertIn('l.last_batch_id IN (SELECT', IN_WORK)
        # Подзапрос «отмечен ли файл водителя» не ссылается на строку — Postgres хэширует его один раз
        # (коррелированный EXISTS по id файла был вдвое медленнее на 45 тыс. водителей).
        self.assertNotIn('IN (l.first_batch_id', IN_WORK)
        self.assertIn('bf_any.department_id = l.department_id AND bf_any.period = l.period', IN_WORK)

    def test_every_pool_text_carries_the_condition(self):
        for name in ('_POOL_SQL', '_POOL_CANDIDATES_SQL'):
            self.assertIn(IN_WORK, getattr(S, name), name)
        # Выдача, подсчёт и проверка подписания перед выдачей — один текст; форматирование не ломается.
        self.assertIn(IN_WORK, S._POOL_SQL.format(lock='FOR UPDATE SKIP LOCKED'))
        # отдел, месяц, часы повтора, попыток, оператор (закреплённые, pins.py), оператор (свои — первыми), предел
        self.assertEqual(S._POOL_SQL.count('%s'), 7)
        self.assertIn(S._POOL_READY_SQL, S._POOL_SQL)
        # Сводка отдела — без оператора, с теми же отметками для общих водителей.
        self.assertIn(IN_WORK, S._POOL_DEPARTMENT_COUNT_SQL)
        self.assertEqual(S._POOL_DEPARTMENT_COUNT_SQL.count('%s'), 4)

    def test_progress_ignores_marks_but_uses_pool_rules_and_month_base(self):
        # «Можно выдать сейчас» у файла — «если отметить этот файл»: без отметок, по правилам пула (тот же текст,
        # только с именами параметров вместо позиций — без единой позиционной метки).
        sql = S._BATCH_PROGRESS_SQL
        self.assertNotIn(IN_WORK, sql)
        named = S._POOL_READY_NAMED_SQL
        self.assertEqual(named.count('%s'), 0)
        self.assertEqual(named, S._POOL_READY_SQL.replace('make_interval(hours => %s)',
                                                          'make_interval(hours => %(retry_hours)s)')
                         .replace('l.attempts_total < %s', 'l.attempts_total < %(max_attempts)s'))
        self.assertIn('FILTER (WHERE' + named + ')', sql)
        self.assertIn(dial_service.month_base.in_month_base_sql("l"), sql)
        self.assertIn('WITH members AS (' + batch_focus.file_members_sql() + ')', sql)
        # «В обзвоне» — как журнал: попытки кончились — уже не в обзвоне.
        self.assertIn("AND l.sign_status <> 'processing' AND l.attempts_total < %(max_attempts)s)", sql)
        self.assertEqual(sql.count('%s'), 0)
        self.assertIn('"retry_hours": settings["retry_after_hours"], "max_attempts": settings["max_attempts"]}',
                      inspect.getsource(S.leads_summary))

    def test_own_callbacks_and_recall_go_past_the_marks(self):
        # Обещанный водителю звонок (перезвон, «дожать» у Тез) не пропадает из-за снятого файла: ни у своего
        # оператора, ни общим пулом после его окна (ревью 17.11.2026).
        self.assertNotIn('dial_list_lead_batches', S._AUTO_OWN_DUE_SQL)
        self.assertIn("OR (l.due_kind IN ('callback', 'online') AND l.owner_call_at IS NOT NULL))", IN_WORK)

    def test_file_membership_is_every_file_the_driver_was_in(self):
        # Третья загрузка не уводит водителя из отмеченного «среднего» файла: принадлежность — first ∪ last ∪
        # записи загрузок, одинаково для пула, прогресса и фильтра «Файл» журнала.
        self.assertIn('l.id IN (SELECT bf_m.lead_id FROM dial_list_batch_leads bf_m', IN_WORK)
        members = batch_focus.file_members_sql()
        for part in ('SELECT ml.first_batch_id AS batch_id, ml.id AS lead_id', 'UNION SELECT ml.last_batch_id, ml.id',
                     'UNION SELECT bl.batch_id, bl.lead_id FROM dial_list_batch_leads bl'):
            self.assertIn(part, members)
        self.assertIn('batch_focus.in_batch_sql("l2", "%(batch_id)s::uuid")', inspect.getsource(S.leads_journal))
        self.assertIn('OR EXISTS (SELECT 1 FROM dial_list_batch_leads bl_in WHERE bl_in.batch_id = %(b)s',
                      batch_focus.in_batch_sql('l2', '%(b)s'))
        importer = inspect.getsource(S.import_leads)
        self.assertIn('INSERT INTO dial_list_batch_leads (batch_id, lead_id) VALUES %s ON CONFLICT DO NOTHING',
                      importer)
        self.assertIn('in_file += [r[0] for r in attached]', importer)
        self.assertIn('RETURNING (xmax = 0) AS inserted, id', S._IMPORT_SQL)

    def test_marks_lock_does_not_wait_for_key_checks_and_old_months_are_cleared(self):
        src = inspect.getsource(S.set_batch_focus)
        self.assertIn('FOR NO KEY UPDATE', src)
        self.assertIn('cur.execute(self._CLEAR_FOCUS_SQL + " AND period <> %s", (department_id, period))', src)
        settings = inspect.getsource(S.save_department_settings)
        self.assertIn('cur.execute(self._CLEAR_FOCUS_SQL, (int(department_id),))', settings)

    def test_journal_parks_only_those_the_pool_would_have_given(self):
        sql = S._JOURNAL_BASE_SQL
        parked = sql.index("THEN 'parked'")
        # Закреплённый за оператором (18.11.2026, pins.py) на паузе не бывает: его выдают мимо отметок.
        from dial_list import pins
        self.assertIn('WHEN NOT ' + pins.department_pool_sql("l") + " THEN 'parked'", sql)
        self.assertIn(IN_WORK, pins.department_pool_sql("l"))
        # Конечные этапы и свои сроки — раньше «паузы»: их пул не выдаёт и без отметок.
        for earlier in ("THEN 'signed'", "THEN 'sign_wait'", "THEN 'excluded'", "THEN 'issued'", "THEN 'callback'",
                        "THEN 'followup'", "THEN 'answered'"):
            self.assertLess(sql.index(earlier), parked, earlier)
        self.assertLess(sql.rindex("THEN 'exhausted'"), parked)
        # «Ждёт повтора» и «В очереди» — после: водителю паузы повтор ничего не даст, пока файл не отмечен.
        self.assertLess(parked, sql.index("THEN 'waiting'"))
        self.assertLess(parked, sql.index("ELSE 'queue'"))
        self.assertEqual(dial_service.LEAD_STAGES["parked"], 'База на паузе')
        self.assertEqual(dial_service.TEZ_LEAD_STAGES["parked"], 'База на паузе')

    def test_schema_adds_the_mark_without_locking_users(self):
        ddl = '\n'.join(dial_schema.DDL)
        self.assertIn('ADD COLUMN IF NOT EXISTS focused BOOLEAN NOT NULL DEFAULT FALSE', ddl)
        self.assertIn('ADD COLUMN IF NOT EXISTS focused_at TIMESTAMP WITH TIME ZONE', ddl)
        focused_by = next(s for s in dial_schema.DDL if 'focused_by' in s and 'ALTER TABLE' in s)
        # FK на users ждал бы её блокировку на каждом старте сервера.
        self.assertEqual(focused_by, 'ALTER TABLE dial_list_lead_batches ADD COLUMN IF NOT EXISTS focused_by INTEGER')
        self.assertTrue(any('idx_dial_list_lead_batches_focused' in s and s.rstrip().endswith('WHERE focused')
                            for s in dial_schema.DDL))


class SetBatchFocusValidationTests(unittest.TestCase):
    MONTH = date(2026, 11, 1)

    def _svc(self):
        svc = S.__new__(S)
        svc.department_settings = lambda department_id: {"_period": self.MONTH}

        class NoDb:
            def _get_cursor(self):
                raise AssertionError('до базы не доходим — запрос отклонён раньше')

        svc.db = NoDb()
        return svc

    def _refused(self, *args, **kwargs):
        with self.assertRaises(dial_service.DialListError) as ctx:
            self._svc().set_batch_focus(*args, **kwargs)
        return str(ctx.exception), ctx.exception.status

    def test_only_the_dialed_month(self):
        message, status = self._refused(1954, [], requester_id=1, period=date(2026, 12, 1))
        self.assertEqual(status, 409)
        self.assertEqual(message, dial_service.BATCH_FOCUS_OTHER_MONTH.format('Ноябрь 2026'))

    def test_body_must_be_a_list_of_file_ids(self):
        self.assertEqual(self._refused(1954, None, requester_id=1)[1], 400)
        self.assertEqual(self._refused(1954, 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', requester_id=1)[1], 400)
        self.assertEqual(self._refused(1954, ['не-файл'], requester_id=1)[1], 400)
        self.assertEqual(self._refused(1954, [{'id': 1}], requester_id=1)[1], 400)

    def test_too_many_files(self):
        many = ['00000000-0000-4000-8000-%012d' % i for i in range(dial_service.BATCH_FOCUS_MAX + 1)]
        self.assertEqual(self._refused(1954, many, requester_id=1)[1], 400)
        # Ровно предел — не отказ по числу: запрос доходит до базы (здесь — до заглушки без базы).
        with self.assertRaises(AssertionError):
            self._svc().set_batch_focus(1954, many[:-1], requester_id=1)


class BatchFocusRouteTests(unittest.TestCase):
    def _client(self, svc):
        import flask
        requester = (5, 'Руководитель', 'x', 'admin', None, None, None, 'admin')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (5, requester, None), is_admin_role=lambda role: True,
            headed_department_ids=lambda rid: [], service=svc)
        app = flask.Flask('dial_list_focus_test')
        app.register_blueprint(bp)
        return app.test_client()

    def test_route_checks_the_body_and_passes_who_and_which_month(self):
        seen = []

        class Svc:
            def manager_scope(self, is_admin, heads, login=None, supervisor_department_code=None, **extra):
                return None

            def set_batch_focus(self, department_id, batch_ids, requester_id=None, period=None):
                seen.append((department_id, batch_ids, requester_id, period))
                return {"focus": {"active": bool(batch_ids)}}

        client = self._client(Svc())
        url = '/api/dial_list/departments/1954/batches/focus'
        resp = client.put(url, json=['a'])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(client.put(url, data='не json', content_type='application/json').status_code, 400)
        self.assertEqual(seen, [])
        resp = client.put(url, json={"batch_ids": ['x'], "period": '2026-11'})
        self.assertEqual((resp.status_code, resp.get_json()), (200, {"status": "success", "focus": {"active": True}}))
        self.assertEqual(seen[-1], (1954, ['x'], 5, date(2026, 11, 1)))
        client.put(url, json={"batch_ids": []})
        self.assertEqual(seen[-1], (1954, [], 5, None))
        # Только PUT: GET с тем же адресом не меняет отметки.
        self.assertEqual(client.get(url).status_code, 405)

    def test_route_refuses_someone_elses_department(self):
        # Глава отдела 560 не переключает обзвон отдела 1954: 403 до сервиса.
        seen = []

        class Svc:
            def manager_scope(self, is_admin, heads, login=None, supervisor_department_code=None, **extra):
                return [560]

            def set_batch_focus(self, *args, **kwargs):
                seen.append(args)
                return {}

        resp = self._client(Svc()).put('/api/dial_list/departments/1954/batches/focus', json={"batch_ids": []})
        self.assertEqual((resp.status_code, resp.get_json()), (403, {"error": "Это не ваш отдел"}))
        self.assertEqual(seen, [])


if __name__ == '__main__':
    unittest.main()
