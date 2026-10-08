# -*- coding: utf-8 -*-
"""Тренинги и отклонения интервалов в «Графиках работы» — чьи отдаёт сервер.

Случай 08.10.2026: супервайзер СЗоВ подтвердил интервал «Тренинг» оператору
другой группы своего отдела. Запись тренинга создалась, флаг дня стал
«подтверждён», а интервал остался «Ожидает»: раздел показывает супервайзеру
всех операторов отдела, список же тренингов сервер отдавал только по его
подчинённым (`u.supervisor_id`). За 60 дней так «зависли» 48 тренингов и 77
отклонений.

Ручки исполняются как код: функции вынимаются из монолита через AST, запрос и
параметры ловит подставной курсор. Импортировать монолит нельзя — он на старте
поднимает пул к базе.
"""

import ast
import calendar
import copy
import logging
import os
import re
import unittest
from contextlib import contextmanager
from datetime import date as dt_date, datetime
from types import SimpleNamespace

from tests import source_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(ROOT, 'bot_schedule2.py')

HANDLERS = ('get_trainings', 'get_training_rejections')
UNDER_TEST = HANDLERS + (
    '_operator_item_id',
    '_filter_operators_for_requester_scope',
    '_work_schedule_operator_ids',
    '_work_schedule_scope_requested',
    '_widen_to_work_schedule_scope',
)

STAFF_LIST_SQL = "SELECT id, role FROM users WHERE role IN ('operator', 'trainee')"


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8-sig') as handle:
        return handle.read()


def _user(user_id, role):
    # Ручки читают только id (0) и роль (3).
    return (user_id, None, 'User %s' % user_id, role, None, None, None)


class _Portal:
    """Маленький портал: два отдела, операторы, их супервайзеры."""

    # id → (роль, отдел, чей подчинённый)
    PEOPLE = {
        2: ('admin', 1, None),        # глава СЗоВ (отдел 1)
        9: ('admin', None, None),     # админ портала, отделом не руководит
        30: ('admin', 1, None),       # глава двух отделов: 1 и 2
        55: ('sv', 1, 2),             # супервайзер из жалобы
        56: ('sv', 1, 2),             # её коллега
        57: ('sv', None, None),       # супервайзер без отдела
        80: ('trainer', 3, None),
        81: ('trainer', None, None),
        500: ('operator', 1, 55),     # подчинённый 55
        518: ('operator', 1, 56),     # оператор из жалобы: отдел тот же, СВ другой
        700: ('operator', 2, None),   # другой отдел
        900: ('trainee', 1, 55),
        901: ('trainee', 1, 56),      # стажёр отдела, числится за коллегой
    }
    HEADS = {2: (1,), 30: (1, 2)}
    TRAINER_PLANNER = {518, 700}      # СЗоВ + ОП у тренера в разделе

    def __init__(self):
        self.queries = []

    def department_of(self, user_id):
        return self.PEOPLE[int(user_id)][1]

    def members(self, department_id):
        return {uid for uid, (_role, dept, _sv) in self.PEOPLE.items() if dept == department_id}

    @contextmanager
    def cursor(self):
        portal = self

        class Cursor:
            def execute(self, sql, params=None):
                portal.queries.append((sql, list(params or [])))
                self._rows = []
                if sql.strip() == STAFF_LIST_SQL:
                    self._rows = sorted(
                        (uid, role) for uid, (role, _dept, _sv) in portal.PEOPLE.items()
                        if role in ('operator', 'trainee'))

            def fetchall(self):
                return self._rows

        yield Cursor()


def _load(portal, requester_id, args):
    role_of = lambda value: str(value or '').strip().lower()
    headed = lambda user_id: tuple(portal.HEADS.get(int(user_id), ()))

    def is_global_admin(role, user_id=None):
        return role_of(role) == 'super_admin' or (role_of(role) == 'admin' and not headed(user_id))

    def load_target(requester, _requester_id, target_id, **_kwargs):
        return _user(int(target_id), portal.PEOPLE[int(target_id)][0]), None

    db = SimpleNamespace(
        _get_cursor=portal.cursor,
        get_user_department_id=portal.department_of,
        get_department_member_ids=portal.members,
        get_user=lambda *, id: _user(int(id), portal.PEOPLE[int(id)][0]),
        get_group=lambda group_id: {'id': group_id, 'department_id': 1},
        supervisor_has_group_access_for_period=lambda *_a, **_k: False,
    )
    namespace = {
        'db': db,
        'request': SimpleNamespace(args=dict(args)),
        'jsonify': lambda payload: payload,
        'logging': logging,
        'datetime': datetime,
        'dt_date': dt_date,
        'calendar': calendar,
        '_get_authenticated_requester': lambda: (
            requester_id, _user(requester_id, portal.PEOPLE[requester_id][0]), None),
        '_normalize_user_role': role_of,
        '_is_admin_role': lambda role: role_of(role) in ('admin', 'super_admin'),
        '_is_supervisor_role': lambda role: role_of(role) == 'sv',
        '_is_global_admin_requester': is_global_admin,
        '_headed_department_id': lambda user_id: next(iter(headed(user_id)), None),
        '_headed_department_ids': lambda user_id: frozenset(headed(user_id)),
        '_department_scope_id_for_requester': lambda user_id: (
            next(iter(headed(user_id)), None) or portal.department_of(user_id)),
        '_trainer_work_schedule_member_ids': lambda: set(portal.TRAINER_PLANNER),
        '_load_target_user_with_scope': load_target,
    }
    module = source_cache.parse(_read('bot_schedule2.py'))
    by_name = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    selected = []
    for name in UNDER_TEST:
        node = copy.deepcopy(by_name[name])
        node.decorator_list = []
        selected.append(node)
    exec(compile(ast.Module(body=selected, type_ignores=[]), BOT_PATH, 'exec'), namespace)  # noqa: S102
    return namespace


def _main_query(queries):
    main = [(sql, params) for sql, params in queries if sql.strip() != STAFF_LIST_SQL]
    assert len(main) == 1, queries
    return ' '.join(main[0][0].split()), main[0][1]


def _ask(handler, requester_id, **args):
    """Позвать ручку и вернуть (WHERE основного запроса, параметры, все запросы).

    Месяц в запросах теста есть всегда, и его условие стоит в WHERE первым —
    по нему внешний WHERE и отличается от WHERE вложенного поиска группы.
    """
    portal = _Portal()
    payload, status = _load(portal, requester_id, args)[handler]()
    assert status == 200, payload
    sql, params = _main_query(portal.queries)
    where = 'TO_CHAR(t.' + sql.split(' WHERE TO_CHAR(t.', 1)[1].rsplit(' ORDER BY ', 1)[0]
    return where, params, portal.queries


def _asked_for_staff(queries):
    return any(sql.strip() == STAFF_LIST_SQL for sql, _params in queries)


MONTH = "TO_CHAR(t.%s, 'YYYY-MM') = %%s"
DATE_COLUMN = {'get_trainings': 'training_date', 'get_training_rejections': 'rejection_date'}


class PlannerScopeTests(unittest.TestCase):
    """scope=work_schedules: прежняя граница + операторы, которых показывает раздел."""

    def _where(self, handler, boundary):
        return (MONTH % DATE_COLUMN[handler]) + ((' AND ' + boundary) if boundary else '')

    def test_supervisor_gets_the_operators_of_the_department(self):
        """Тот самый случай: оператор 518 — отдел общий, супервайзер другой."""
        for handler in HANDLERS:
            where, params, _ = _ask(handler, 55, month='2026-10', scope='work_schedules')
            self.assertEqual(
                where, self._where(handler, '(u.supervisor_id = %s OR t.operator_id = ANY(%s))'), handler)
            self.assertEqual(params, ['2026-10', 55, [500, 518, 900, 901]], handler)

    def test_a_trainee_of_the_department_is_added(self):
        """Строка стажёра в разделе тоже есть, и тренинг ему супервайзер записать
        может — значит, обязан и увидеть, чей бы стажёр ни был."""
        for handler in HANDLERS:
            _where, params, _ = _ask(handler, 55, month='2026-10', scope='work_schedules')
            self.assertIn(901, params[-1], handler)

    def test_a_colleague_supervisor_is_not_added(self):
        """Тренинги коллеги-СВ (в том числе дисциплинарные) супервайзеру раздел
        так и не открывает; оператор чужого отдела — тоже."""
        for handler in HANDLERS:
            _where, params, _ = _ask(handler, 55, month='2026-10', scope='work_schedules')
            self.assertNotIn(56, params[-1], handler)
            self.assertNotIn(2, params[-1], '%s: глава отдела' % handler)
            self.assertNotIn(700, params[-1], '%s: оператор чужого отдела' % handler)

    def test_without_the_scope_nothing_changes(self):
        """«Тренинги», «Учёт часов» и старые вкладки шлют запрос без scope."""
        for handler in HANDLERS:
            where, params, queries = _ask(handler, 55, month='2026-10')
            self.assertEqual(where, self._where(handler, 'u.supervisor_id = %s'), handler)
            self.assertEqual(params, ['2026-10', 55], handler)
            self.assertFalse(_asked_for_staff(queries), handler)

    def test_an_addressed_request_keeps_its_own_boundary(self):
        """?id= и ?group_id= уже сказали, чьи тренинги нужны."""
        for handler in HANDLERS:
            where, params, queries = _ask(handler, 55, month='2026-10', scope='work_schedules', id='56')
            self.assertEqual(where, self._where(handler, 'u.supervisor_id = %s'), handler)
            self.assertEqual(params, ['2026-10', 56], handler)
            self.assertFalse(_asked_for_staff(queries), handler)

            where, params, queries = _ask(handler, 55, month='2026-10', scope='work_schedules', group_id='8')
            self.assertNotIn('ANY(%s)', where, handler)
            self.assertIn('gom.group_id = %s', where, handler)
            self.assertEqual(params, ['2026-10', 8], handler)
            self.assertFalse(_asked_for_staff(queries), handler)

    def test_department_head_gets_every_headed_department(self):
        """Глава двух отделов видит в разделе оба, а тренинги шли только по первому."""
        for handler in HANDLERS:
            where, params, _ = _ask(handler, 30, month='2026-10', scope='work_schedules')
            self.assertEqual(
                where, self._where(handler, '(u.department_id = %s OR t.operator_id = ANY(%s))'), handler)
            self.assertEqual(params, ['2026-10', 1, [500, 518, 700, 900, 901]], handler)

    def test_trainer_keeps_his_own_department(self):
        """Тренер раздел только смотрит и ничего в нём не подтверждает. Операторов
        чужого отдела (раздел показывает ему СЗоВ и ОП) scope ему не открывает."""
        for handler in HANDLERS:
            where, params, queries = _ask(handler, 80, month='2026-10', scope='work_schedules')
            self.assertEqual(where, self._where(handler, 'u.department_id = %s'), handler)
            self.assertEqual(params, ['2026-10', 3], handler)
            self.assertFalse(_asked_for_staff(queries), handler)

            where, params, queries = _ask(handler, 81, month='2026-10', scope='work_schedules')
            self.assertEqual(where, self._where(handler, 'FALSE'), handler)
            self.assertEqual(params, ['2026-10'], handler)
            self.assertFalse(_asked_for_staff(queries), handler)

    def test_supervisor_without_department_keeps_only_direct_reports(self):
        """Раздел по старой памяти показывает ему всех операторов; чужие тренинги
        это не открывает — записать занятие он может только подчинённому."""
        for handler in HANDLERS:
            where, params, queries = _ask(handler, 57, month='2026-10', scope='work_schedules')
            self.assertEqual(where, self._where(handler, 'u.supervisor_id = %s'), handler)
            self.assertEqual(params, ['2026-10', 57], handler)
            self.assertFalse(_asked_for_staff(queries), handler)

    def test_operator_still_reads_only_his_own(self):
        """«Мои смены» грузят тренинги тем же запросом — оператору scope ничего
        не добавляет и список операторов ради него не читается."""
        for handler in HANDLERS:
            for requester_id in (518, 900):
                where, params, queries = _ask(handler, requester_id, month='2026-10', scope='work_schedules')
                self.assertEqual(where, self._where(handler, 't.operator_id = %s'), handler)
                self.assertEqual(params, ['2026-10', requester_id], handler)
                self.assertFalse(_asked_for_staff(queries), handler)

    def test_portal_admin_has_no_boundary_to_widen(self):
        for handler in HANDLERS:
            where, params, queries = _ask(handler, 9, month='2026-10', scope='work_schedules')
            self.assertEqual(where, self._where(handler, ''), handler)
            self.assertEqual(params, ['2026-10'], handler)
            self.assertFalse(_asked_for_staff(queries), handler)

    def test_placeholders_match_the_parameters(self):
        """Параметр списка операторов обязан идти последним — как и его место в запросе."""
        for handler in HANDLERS:
            for requester_id in sorted(_Portal.PEOPLE):
                for extra in ({}, {'scope': 'work_schedules'}):
                    _where, params, queries = _ask(handler, requester_id, month='2026-10', **extra)
                    sql, _params = _main_query(queries)
                    self.assertEqual(sql.count('%s'), len(params), (handler, requester_id, extra))
                    if any(isinstance(value, list) for value in params):
                        self.assertTrue(
                            sql.rsplit(' ORDER BY ', 1)[0].endswith('t.operator_id = ANY(%s))'),
                            (handler, requester_id, extra))
                    for index, value in enumerate(params):
                        if isinstance(value, list):
                            self.assertEqual(index, len(params) - 1, (handler, requester_id, extra))

    def test_the_scope_value_is_exact(self):
        for handler in HANDLERS:
            where, _params, _ = _ask(handler, 55, month='2026-10', scope='everything')
            self.assertNotIn('ANY(%s)', where, handler)
            where, _params, _ = _ask(handler, 55, month='2026-10', scope=' Work_Schedules ')
            self.assertIn('ANY(%s)', where, handler)


class SectionPerimeterTests(unittest.TestCase):
    """Операторов раздела считает та же функция, что режет его список."""

    def _ids(self, requester_id):
        portal = _Portal()
        namespace = _load(portal, requester_id, {})
        requester = _user(requester_id, portal.PEOPLE[requester_id][0])
        ids = namespace['_work_schedule_operator_ids'](requester, requester_id)
        staff = [{'id': uid, 'role': role}
                 for uid, (role, _dept, _sv) in sorted(portal.PEOPLE.items()) if role in ('operator', 'trainee')]
        shown = [item['id'] for item in namespace['_filter_operators_for_requester_scope'](
            requester, requester_id, staff)]
        return ids, shown

    def test_it_matches_the_list_of_the_section(self):
        for requester_id in (2, 30, 55, 56):
            ids, shown = self._ids(requester_id)
            self.assertEqual(ids, shown, requester_id)
            self.assertTrue(ids, requester_id)

    def test_no_boundary_for_the_portal_admin(self):
        ids, _shown = self._ids(9)
        self.assertIsNone(ids)

    def test_nobody_for_those_who_confirm_nothing(self):
        """Оператор и стажёр раздела не имеют, тренер его только смотрит, СВ без
        отдела пишет только подчинённым."""
        for requester_id in (518, 900, 57, 80, 81):
            ids, _shown = self._ids(requester_id)
            self.assertEqual(ids, [], requester_id)

    def test_the_helper_calls_the_section_filter(self):
        source = _read('bot_schedule2.py')
        body = source.split('def _work_schedule_operator_ids(', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('_filter_operators_for_requester_scope(requester, requester_id, staff)', body)


class PlannerLoaderTests(unittest.TestCase):
    """«Графики работы» спрашивают тренинги своим периметром и заменяют месяц целиком."""

    @classmethod
    def setUpClass(cls):
        cls.app = _read('src', 'App.jsx')

    def _loader(self, name):
        return self.app.split('const %s = useCallback(async (monthKey' % name, 1)[1].split(
            '}, [API_BASE_URL, user?.id, withAccessTokenHeader]);', 1)[0]

    def test_both_lists_ask_with_the_section_scope(self):
        self.assertIn(
            '/api/trainings?month=${encodeURIComponent(normalizedMonth)}&scope=work_schedules`',
            self._loader('fetchPlannerTrainingsForMonth'))
        self.assertIn(
            '/api/training_rejections?month=${encodeURIComponent(normalizedMonth)}&scope=work_schedules`',
            self._loader('fetchPlannerTrainingRejectionsForMonth'))

    def test_other_sections_do_not_send_the_scope(self):
        """«Тренинги» и «Учёт часов» видят прежний состав — их периметр не менялся."""
        self.assertEqual(len(re.findall(r'[?&]scope=work_schedules', self.app)), 2)
        self.assertNotIn('work_schedules', _read('src', 'components', 'trainings', 'TrainingsView.jsx'))

    def test_the_month_is_replaced_not_merged(self):
        """Удалённый тренинг оставался на экране до перезагрузки страницы."""
        self.assertIn(
            'setPlannerTrainingsByOperator(prev => replaceMonthRows(prev, rows, normalizedMonth));',
            self._loader('fetchPlannerTrainingsForMonth'))
        self.assertIn(
            'setPlannerTrainingRejectionsByOperator(prev => replaceMonthRows(prev, rows, normalizedMonth));',
            self._loader('fetchPlannerTrainingRejectionsForMonth'))
        self.assertEqual(self.app.count('setPlannerTrainingsByOperator('), 1)
        self.assertEqual(self.app.count('setPlannerTrainingRejectionsByOperator('), 1)

    def test_a_late_answer_does_not_replace_a_fresh_month(self):
        for name, ref, loaded in (
                ('fetchPlannerTrainingsForMonth', 'plannerTrainingMonthRequestsRef',
                 'plannerLoadedTrainingMonthKeysRef'),
                ('fetchPlannerTrainingRejectionsForMonth', 'plannerRejectionMonthRequestsRef',
                 'plannerLoadedRejectionMonthKeysRef')):
            loader = self._loader(name)
            self.assertIn('trackMonthRequest(%s.current, normalizedMonth)' % ref, loader)
            # Билет берёт только запрос, который действительно уходит: пустой вызов
            # («месяц уже загружен») не должен старить ответ, который ещё в полёте.
            self.assertLess(
                loader.index('%s.current.has(normalizedMonth)) return;' % loaded),
                loader.index('trackMonthRequest('), name)
            self.assertLess(loader.index('trackMonthRequest('), loader.index('await fetch('), name)
            self.assertLess(loader.index('if (!acceptAnswer()) return'), loader.index('replaceMonthRows('), name)
            # Месяц считается загруженным, только когда его ответ применён.
            self.assertLess(
                loader.index('if (!acceptAnswer()) return'),
                loader.index('%s.current.add(normalizedMonth)' % loaded), name)

    def test_each_list_counts_its_own_requests(self):
        """Оба списка всегда спрашиваются парой на один месяц: общий счётчик
        состарил бы ответ тренингов ответом отклонений."""
        self.assertEqual(self.app.count('const plannerTrainingMonthRequestsRef = useRef({});'), 1)
        self.assertEqual(self.app.count('const plannerRejectionMonthRequestsRef = useRef({});'), 1)
        self.assertEqual(self.app.count('plannerTrainingMonthRequestsRef'), 2)
        self.assertEqual(self.app.count('plannerRejectionMonthRequestsRef'), 2)

    def test_the_match_report_still_gets_the_rows(self):
        """«Отчёт соответствия» берёт тренинги из ответа загрузчика — запоздавший
        ответ экран не трогает, но строки вернуть обязан."""
        self.assertIn('if (!acceptAnswer()) return rows;', self._loader('fetchPlannerTrainingsForMonth'))


class DayWindowTests(unittest.TestCase):
    """Окно дня: чужие решения, сбой сохранения, правка сохранённого тренинга."""

    @classmethod
    def setUpClass(cls):
        cls.app = _read('src', 'App.jsx')
        cls.submit = cls.app.split('const submitPlannerTrainingModal = async () => {', 1)[1].split(
            'const deletePlannerTraining', 1)[0]
        cls.reject = cls.app.split('const rejectPlannerTrainingIntervals = async (intervals) => {', 1)[1].split(
            'const modalTechReasonStatusSegments', 1)[0]

    def test_a_day_with_training_intervals_asks_both_lists_again(self):
        """Интервал мог подтвердить другой супервайзер или вторая вкладка: месяц
        грузится один раз, и без перезапроса он оставался «Ожидает»."""
        effect = self.app.split(
            '}, [modalTrainingNewsKey, modalHasTrainingSegments, fetchPlannerTrainingNews]);', 1)[1].split(
            'const modalTrainingNewsWindows', 1)[0]
        self.assertIn('if (!modalTrainingNewsKey || !modalHasTrainingSegments) return;', effect)
        self.assertIn("const monthKey = modalTrainingNewsKey.split('|')[1].slice(0, 7);", effect)
        self.assertIn('fetchPlannerTrainingsForMonth(monthKey, { force: true })', effect)
        self.assertIn('fetchPlannerTrainingRejectionsForMonth(monthKey, { force: true })', effect)
        self.assertIn(
            '}, [modalTrainingNewsKey, modalHasTrainingSegments, fetchPlannerTrainingsForMonth, '
            'fetchPlannerTrainingRejectionsForMonth]);', effect)

    def test_a_failed_save_shows_what_did_get_saved(self):
        """«Подтвердить все»: первый интервал записан, второй дал отказ — без
        перезапроса на экране оставались все «Ожидает», а повтор упирался в первый."""
        failed = self.submit.split("console.error('Error saving planner training:', error);", 1)[1].split(
            '} finally {', 1)[0]
        self.assertIn(
            'fetchPlannerTrainingsForMonth(dayKey.slice(0, 7), { force: true }).catch(() => {});', failed)
        failed = self.reject.split("console.error('Error rejecting training intervals:', error);", 1)[1].split(
            '} finally {', 1)[0]
        self.assertIn(
            'fetchPlannerTrainingRejectionsForMonth(dayKey.slice(0, 7), { force: true }).catch(() => {});', failed)

    def test_the_overlap_refusal_is_in_russian(self):
        """Сервер отвечает по-английски; супервайзер видел «Training overlaps…»."""
        russian = "payload?.overlap ? 'У оператора уже есть тренинг, который пересекается по времени.' : "
        self.assertEqual(self.submit.count(russian), 2, 'и запись (POST), и правка (PUT)')
        self.assertNotIn('throw new Error(payload?.error ||', self.submit)

    def test_editing_a_saved_training_keeps_its_hours_flag(self):
        """Правка времени из окна дня молча делала тренинг «Не в часах» оплачиваемым:
        окно открывалось без признака, а сохранялось с count_in_hours: true."""
        presets = self.app.split("openPlannerTrainingModalForCurrentDay('edit', {")[1:]
        self.assertEqual(len(presets), 2, 'окно дня на компьютере и на телефоне')
        for preset in presets:
            self.assertIn('countInHours: t?.count_in_hours !== false', preset.split('})}', 1)[0])
        self.assertIn(
            "countInHours: typeof preset?.countInHours === 'boolean' ? preset.countInHours : true,", self.app)
        self.assertEqual(self.submit.count('count_in_hours: !!plannerTrainingModalState?.countInHours'), 2)


if __name__ == '__main__':
    unittest.main()
