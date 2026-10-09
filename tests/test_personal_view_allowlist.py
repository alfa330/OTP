# -*- coding: utf-8 -*-
"""Личный набор разделов: человеку портал показывает только перечисленное.

Решение владельца 06.10.2026 про сотрудника отдела аналитики: «чтобы у этого
сотрудника отображался только раздел байга без qr, полный доступ к разделу».
Доступ и замок раздела сторожит tests/test_baiga.py; здесь — то, что портал
показывает человеку ПОМИМО раздела, то есть ничего.

Что сторожится:
  * набор один и тот же во фронте и на сервере (departmentViews.js и
    bot_schedule2.py), и считают его обе стороны одинаково — на сетке id, ролей
    и главенства;
  * человек набора свой раздел действительно получает: без допуска в сам раздел
    он увидел бы пустой портал;
  * колокол молчит об источниках, которые зовут только в скрытые разделы, а
    карта «источник → разделы» совпадает с тем, что источники пишут на деле;
  * проводка в App.jsx: «Вики», «Библиотека» и «Ивенты» спрашивают набор, бар
    телефона и вход в портал — тоже.

Поведение предикатов фронта — tests/personal_view_allowlist.test.mjs.
"""

import ast
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baiga import access as baiga_access  # noqa: E402
from notifications import sources  # noqa: E402
from tests import source_cache  # noqa: E402

APP_JSX = ROOT / 'src' / 'App.jsx'
VIEWS_JS = ROOT / 'src' / 'utils' / 'departmentViews.js'
BOT_PY = ROOT / 'bot_schedule2.py'
SOURCES_PY = ROOT / 'notifications' / 'sources.py'

# Только id: ФИО в публичный репозиторий не кладём.
ANALYST_ID = 540

ROLES = ('super_admin', 'admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee',
         'marketing_manager', 'accounting_manager', 'hr_manager')


def _read(path):
    return path.read_text(encoding='utf-8-sig')


def _function_source(name):
    source = source_cache.read(BOT_PY)
    node = source_cache.function_node(BOT_PY, name)
    # Срез по номерам строк узла: ast.get_source_segment на монолите — 0,3 с.
    return textwrap.dedent('\n'.join(source.splitlines()[node.lineno - 1:node.end_lineno]))


def _module_literal(name):
    """Значение присваивания `name = <литерал>` верхнего уровня монолита."""
    for node in source_cache.tree(BOT_PY).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError('%s не найден в bot_schedule2.py' % name)


def _front_allowlist():
    """Карта из departmentViews.js: {id: (разделы…)}."""
    block = re.search(r'const PERSONAL_VIEW_ALLOWLIST = \{\n(.*?)\n\};', _read(VIEWS_JS), re.S)
    assert block, 'PERSONAL_VIEW_ALLOWLIST не найден в departmentViews.js'
    entries = {}
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith('//'):
            continue
        match = re.fullmatch(r"(\d+): \[((?:'[a-z_]+'(?:, )?)*)\],", line)
        assert match, 'строка карты не разобрана: %r' % line
        entries[int(match.group(1))] = tuple(re.findall(r"'([a-z_]+)'", match.group(2)))
    return entries


def _server_namespace(heads=(), allowlist=None):
    """Настоящие функции монолита о личном наборе — с подставленным главенством.

    allowlist — свой набор вместо боевого: в боевом один человек и один раздел,
    и правило «источник остаётся, пока открыт его раздел» иначе не исполнялось бы."""
    class _Db:
        """Отдел спрашивают у рядового без личного набора — ради набора отдела
        (tests/test_remote_cc_head_and_views.py). Здесь все из отдела аналитики,
        а у него набора нет."""

        def get_user_department(self, _user_id):
            return (2134, 'analytik')

    namespace = {
        'db': _Db(),
        'PERSONAL_VIEW_ALLOWLIST': (allowlist if allowlist is not None
                                    else _module_literal('PERSONAL_VIEW_ALLOWLIST')),
        'PERSONAL_VIEW_BASE_ROLES': _module_literal('PERSONAL_VIEW_BASE_ROLES'),
        'DEPARTMENT_ONLY_VIEWS': _module_literal('DEPARTMENT_ONLY_VIEWS'),
        'BACK_OFFICE_EMPLOYEE_ROLES': frozenset(
            _module_literal('BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE').values()),
        '_headed_department_id': lambda requester_id: 2134 if requester_id in heads else None,
    }
    for name in ('_normalize_user_role', '_personal_views_for'):
        exec(_function_source(name), namespace)
    return namespace


class MirrorTests(unittest.TestCase):

    def test_the_set_is_the_same_on_both_sides(self):
        front = _front_allowlist()
        server = _module_literal('PERSONAL_VIEW_ALLOWLIST')
        self.assertEqual(front, {user_id: tuple(views) for user_id, views in server.items()})
        self.assertEqual(front, {ANALYST_ID: ('baiga',)})

    def test_everyone_in_the_set_really_gets_the_section(self):
        """Набор только прячет остальное. Человек из набора, которому сам
        раздел не выдан, увидел бы пустой портал."""
        granted_by = {'baiga': baiga_access.ANALYST_USER_IDS}
        for user_id, views in _front_allowlist().items():
            self.assertTrue(views, user_id)
            for view in views:
                self.assertIn(view, granted_by, 'раздел %s: допиши, каким списком он выдаётся' % view)
                self.assertIn(user_id, granted_by[view], (user_id, view))

    def test_server_reads_the_set_like_the_frontend(self):
        """personalViewsOf (фронт) и _personal_views_for (сервер) — одно
        правило: набор по id и только рядовому, не возглавляющему отдел."""
        node = shutil.which('node')
        if not node:
            self.skipTest('node недоступен')
        cases = [(user_id, role, head)
                 for user_id in (ANALYST_ID, str(ANALYST_ID), ANALYST_ID + 1, 229, None)
                 for role in ROLES + (' Operator ', 'Supervisor', '') for head in (False, True)]
        users = [{'id': user_id, 'role': role, 'department_code': 'analytik',
                  'headed_department_id': 2134 if head else None} for user_id, role, head in cases]
        script = '\n'.join((
            "import { personalViewsOf } from '%s';" % VIEWS_JS.as_uri(),
            'const users = %s;' % json.dumps(users),
            'process.stdout.write(JSON.stringify(users.map((u) => personalViewsOf(u))));',
        ))
        out = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True, check=True)
        front = json.loads(out.stdout.decode('utf-8'))
        self.assertEqual(len(front), len(cases))
        restricted = 0
        for (user_id, role, head), answer in zip(cases, front):
            namespace = _server_namespace(heads={user_id} if head else ())
            # Роль отдаём как есть, без приведения: его делает сама функция.
            server = namespace['_personal_views_for'](user_id, role)
            self.assertEqual(answer, list(server) if server is not None else None, (user_id, role, head))
            restricted += answer is not None
        # Сетка не вырождена: набор и действует, и снимается. Действует он у
        # двух записей id (число и строка) на пяти рядовых ролях и на роли в
        # другом регистре — без главенства.
        self.assertEqual(restricted, 2 * 6)
        self.assertLess(restricted, len(cases) - 10)

    def test_empty_set_means_no_set_on_both_sides(self):
        """Пустой набор — ошибка записи, а не «спрятать всё»: обе стороны
        читают его как отсутствие набора. Иначе фронт показал бы человеку
        пустой портал, а колокол звал бы его в разделы, которых в меню нет."""
        self.assertIsNone(_server_namespace(allowlist={ANALYST_ID: ()})['_personal_views_for'](ANALYST_ID, 'operator'))
        node = shutil.which('node')
        if not node:
            self.skipTest('node недоступен')
        source = _read(VIEWS_JS)
        emptied, replaced = re.subn(r"(const PERSONAL_VIEW_ALLOWLIST = \{\n\s+%d: )\['baiga'\]" % ANALYST_ID,
                                    r'\1[]', source)
        self.assertEqual(replaced, 1)
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'roles.js').write_text(_read(VIEWS_JS.with_name('roles.js')), encoding='utf-8')
            copy = Path(folder) / 'departmentViews.js'
            copy.write_text(emptied, encoding='utf-8')
            script = '\n'.join((
                "import { personalViewsOf, departmentRestrictsViews, departmentAllowsView } from '%s';" % copy.as_uri(),
                "const user = { id: %d, role: 'operator', department_code: 'analytik' };" % ANALYST_ID,
                'process.stdout.write(JSON.stringify([personalViewsOf(user), departmentRestrictsViews(user),',
                "    departmentAllowsView(user, 'hours'), departmentAllowsView(user, 'events')]));",
            ))
            out = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True, check=True)
        self.assertEqual(json.loads(out.stdout.decode('utf-8')), [None, False, True, True])

    def test_server_rule_by_hand(self):
        views = _server_namespace()['_personal_views_for']
        self.assertEqual(views(ANALYST_ID, 'operator'), ('baiga',))
        self.assertEqual(views(str(ANALYST_ID), 'operator'), ('baiga',))
        for role in ('trainee', 'hr_manager', 'accounting_manager', 'marketing_manager'):
            self.assertEqual(views(ANALYST_ID, role), ('baiga',), role)
        self.assertIsNone(views(ANALYST_ID + 1, 'operator'))
        self.assertIsNone(views(None, 'operator'))
        self.assertIsNone(views('abc', 'operator'))
        # Набор — только рядовому: повысили человека — меню его новой роли.
        for role in ('sv', 'supervisor', 'trainer', 'admin', 'super_admin', '', None):
            self.assertIsNone(views(ANALYST_ID, role), role)
        # И не главе отдела, какой бы ни была его базовая роль.
        headed = _server_namespace(heads={ANALYST_ID})['_personal_views_for']
        for role in ('operator', 'marketing_manager', 'admin'):
            self.assertIsNone(headed(ANALYST_ID, role), role)

    def test_rank_and_file_is_the_same_list_on_both_sides(self):
        """Набор проверен на ветке рядового сотрудника сайдбара. Разойдись
        списки ролей — сервер прятал бы колокол тому, у кого меню полное."""
        server = set(_module_literal('PERSONAL_VIEW_BASE_ROLES')) | set(
            _module_literal('BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE').values())
        views = _read(VIEWS_JS)
        base = re.search(r"const PERSONAL_VIEW_BASE_ROLES = \[([^\]]*)\];", views)
        back_office = re.search(r'const BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT = \{(.*?)\};', views, re.S)
        front = set(re.findall(r"'([a-z_]+)'", base.group(1))) | set(
            re.findall(r":\s*'([a-z_]+)'", back_office.group(1)))
        self.assertEqual(server, front)
        self.assertEqual(server, {'operator', 'trainee', 'hr_manager', 'accounting_manager', 'marketing_manager'})
        # И это ровно роли ветки рядового в меню.
        self.assertIn("const RANK_AND_FILE_ROLES = Object.freeze(['operator', 'trainee', "
                      "...BACK_OFFICE_EMPLOYEE_ROLES]);", _read(APP_JSX))


class BellTests(unittest.TestCase):
    """Колокол не зовёт человека в разделы, которых у него нет в меню."""

    def test_sources_that_only_lead_to_hidden_sections_are_silent(self):
        hidden = sources.sources_outside_views(('baiga',))
        self.assertEqual(set(hidden), {
            'wiki_ack', 'tasks', 'wiki_questions', 'checkpoints', 'shift_requests',
            'lms', 'surveys', 'events', 'four_you'})
        # «Обращения» и «Жалобы» — разделы со своим кругом доступа: набор их в
        # меню не прячет, значит, и колокол о них не молчит.
        self.assertNotIn('crm', hidden)
        self.assertNotIn('complaints', hidden)
        # Строки без перехода никуда не зовут — их и не прячем: день рождения
        # коллеги и свой назначенный тренинг читают в самом колоколе.
        self.assertNotIn('birthdays', hidden)
        self.assertNotIn('training_plans', hidden)
        # Порядок — как у источников: по нему сводка их и обходит.
        self.assertEqual(list(hidden), [name for name in sources.SOURCES if name in hidden])

    def test_a_source_stays_while_any_of_its_sections_is_open(self):
        self.assertNotIn('tasks', sources.sources_outside_views(('baiga', 'tasks')))
        for view in ('evaluation', 'call_evaluation'):
            self.assertNotIn('checkpoints', sources.sources_outside_views((view,)), view)
        hidden = sources.sources_outside_views(('wiki',))
        self.assertNotIn('wiki_ack', hidden)
        self.assertNotIn('wiki_questions', hidden)
        self.assertIn('events', hidden)
        # Пустой набор — скрыто всё, что куда-то ведёт.
        self.assertEqual(sources.sources_outside_views(()), sources.sources_outside_views(None))
        self.assertIn('events', sources.sources_outside_views(()))

    def test_own_circle_sections_are_exactly_those_the_guard_lets_through(self):
        """Колокол молчит ровно о тех разделах, которые набор прячет в меню.
        Раздел, который гард «Этап 10» пропускает своим предикатом раньше
        набора, набор не прячет — и его источник обязан остаться. Флаги «Вики»
        и «Библиотеки» не в счёт: они сами спрашивают набор."""
        app = _read(APP_JSX)
        start = app.index('// Гард видимости разделов по отделу (Этап 10)')
        guard = app[start:app.index('wikiSectionEnabled, view]);', start)]
        own = dict(re.findall(r"if \(view === '([a-z_]+)' && (\w+)\) return;", guard))
        asks_the_set = {'wikiSectionEnabled', 'canAccessLibrarySection'}
        for flag in asks_the_set:
            self.assertRegex(app, r"const %s = [^;]*personalViewsAllow\(user, '[a-z]+'\);" % flag)
        bell_views = {view for views in sources.SOURCE_VIEWS.values() for view in views if view}
        passed_by_own_predicate = {view for view in bell_views
                                   if view in own and own[view] not in asks_the_set}
        self.assertEqual(passed_by_own_predicate, set(sources.OWN_CIRCLE_VIEWS))
        # «Оплата счетов» (#381) — тоже свой круг: раздел открыт поимённо.
        self.assertEqual(set(sources.OWN_CIRCLE_VIEWS), {'crm_tickets', 'complaints', 'payments'})
        # И наоборот: названный в наборе раздел со своим кругом ничего не меняет.
        self.assertEqual(sources.sources_outside_views(('baiga', 'crm_tickets')),
                         sources.sources_outside_views(('baiga',)))

    def test_unknown_source_is_not_hidden(self):
        """Источник, забытый в карте, должен стоить лишнего уведомления, а не
        пропавшего."""
        original = sources.SOURCE_VIEWS
        try:
            sources.SOURCE_VIEWS = {name: views for name, views in original.items() if name != 'events'}
            self.assertNotIn('events', sources.sources_outside_views(('baiga',)))
        finally:
            sources.SOURCE_VIEWS = original

    def test_map_matches_what_the_sources_really_write(self):
        """Карта «источник → разделы» — копия поля 'view' из самих источников.
        Разойдись они, колокол спрячет источник, который ведёт в открытый
        раздел, или оставит тот, что зовёт в скрытый."""
        self.assertEqual(set(sources.SOURCE_VIEWS), set(sources.SOURCES))
        # Помощники, которые собирают строки за источник.
        helpers = {'_complaint_author_items': 'crm'}
        written = {name: set() for name in sources.SOURCES}
        tree = ast.parse(_read(SOURCES_PY))
        for function in tree.body:
            if not isinstance(function, ast.FunctionDef):
                continue
            views = set()
            for node in ast.walk(function):
                if not isinstance(node, ast.Dict):
                    continue
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == 'view':
                        self.assertIsInstance(value, ast.Constant, function.name)
                        views.add(value.value)
            if not views:
                continue
            owner = helpers.get(function.name, function.name)
            self.assertIn(owner, written, 'строки с переходом вне источника: %s' % function.name)
            written[owner] |= views
        for name in sources.SOURCES:
            self.assertEqual(set(sources.SOURCE_VIEWS[name]), written[name], name)

    def test_sections_of_the_map_exist_in_the_portal(self):
        app = _read(APP_JSX)
        for name, views in sources.SOURCE_VIEWS.items():
            for view in views:
                if view is None:
                    continue
                self.assertRegex(app, r"view === ['\"]%s['\"]" % view, (name, view))

    def _viewer(self, requester_id, role, *, can_see_tasks=False, heads=(), allowlist=None, payments_open=True):
        """Настоящий _notifications_viewer_context с подставленными соседями.

        payments_open — открыт ли человеку раздел «Оплата счетов»: у него свой
        поимённый периметр (payments/access.py), к личному набору он отношения не
        имеет, поэтому по умолчанию в проверках набора он «открыт»."""
        namespace = _server_namespace(heads=heads, allowlist=allowlist)
        namespace.update({
            '_events_viewer_scope': lambda requester_id, role: (False, 2134),
            '_four_you_access_for_requester': lambda requester_id, requester: (False, None),
            '_can_access_tasks': lambda role, requester_id: can_see_tasks,
            '_payments_section_open_for': lambda user_id: payments_open,
            '_birthdays_viewer_scope': lambda requester_id, role, **kwargs: (False, 2134),
            '_checkpoint_scope_for_requester': lambda requester_id, requester: {},
            '_can_manage_checkpoints': lambda requester_id, requester: False,
            '_shift_change_scope_for_requester': lambda requester_id, requester: {},
        })
        exec(_function_source('_notifications_viewer_context'), namespace)
        return namespace['_notifications_viewer_context'](requester_id, (requester_id, None, None, role))

    def test_bell_of_the_named_person_is_silent_about_hidden_sections(self):
        viewer = self._viewer(ANALYST_ID, 'operator')
        self.assertEqual(set(viewer['hidden_sources']),
                         {'tasks'} | set(sources.sources_outside_views(('baiga',))))
        self.assertEqual(len(viewer['hidden_sources']), len(set(viewer['hidden_sources'])))
        # Раздел «Задачи» открыт ролью — набор его всё равно прячет: в меню его нет.
        self.assertIn('tasks', self._viewer(ANALYST_ID, 'marketing_manager',
                                            can_see_tasks=True)['hidden_sources'])

        # Сводка не зовёт ни один скрытый источник — и считает в них ноль.
        called = []
        original = sources._HANDLERS
        try:
            sources._HANDLERS = {
                name: (lambda cursor, viewer, limit, name=name: called.append(name) or (3, []))
                for name in original
            }

            class Cursor:
                def execute(self, *args, **kwargs):
                    pass

                def fetchone(self):
                    return (None,)

                def fetchall(self):
                    return []

            counts, _items, _meta = sources.collect(Cursor(), viewer)
        finally:
            sources._HANDLERS = original
        # Источники со своим кругом (crm, complaints, payments) набор не прячет:
        # их гасит собственный периметр раздела — здесь он открыт.
        self.assertEqual(set(called), {'birthdays', 'training_plans', 'crm', 'complaints', 'payments'})
        for name in viewer['hidden_sources']:
            self.assertEqual(counts[name], 0, name)
        self.assertEqual(counts['total'], 3 * len(set(called)))

    def test_set_neither_reopens_nor_closes_what_it_does_not_name(self):
        """Набор только прячет: раздел из набора, закрытый человеку ролью,
        колокол не открывает, а открытый ролью и названный в наборе — оставляет."""
        allowlist = {ANALYST_ID: ('baiga', 'tasks', 'events')}
        closed_by_role = self._viewer(ANALYST_ID, 'operator', allowlist=allowlist)['hidden_sources']
        self.assertIn('tasks', closed_by_role)
        self.assertNotIn('events', closed_by_role)
        self.assertIn('surveys', closed_by_role)
        open_by_role = self._viewer(ANALYST_ID, 'accounting_manager', can_see_tasks=True,
                                    allowlist=allowlist)['hidden_sources']
        self.assertNotIn('tasks', open_by_role)
        self.assertNotIn('events', open_by_role)
        self.assertIn('wiki_ack', open_by_role)

    def test_bell_blueprint_gets_this_very_viewer(self):
        """Портрет зрителя доходит до колокола как есть: подмена на сборке
        блюпринта (свой словарь, урезанный портрет) вернула бы человеку с
        набором уведомления о разделах, которых у него нет."""
        bot = source_cache.read(BOT_PY)
        block = bot.split('from notifications.routes import build_notifications_blueprint')[1].split('except Exception')[0]
        self.assertIn('app.register_blueprint(build_notifications_blueprint(', block)
        self.assertIn('        viewer_context=_notifications_viewer_context,\n', block)
        routes = _read(ROOT / 'notifications' / 'routes.py')
        self.assertIn('return viewer_context(requester_id, requester), None', routes)

    def test_bell_is_silent_about_payments_outside_its_perimeter(self):
        """«Оплата счетов» открыта поимённо: кому раздел не открыт, тому колокол о нём
        молчит — уведомление звало бы туда, куда человека не пустят. Личный набор
        тут ни при чём: правило своё и действует на всех."""
        self.assertEqual(self._viewer(ANALYST_ID + 1, 'operator', payments_open=False)['hidden_sources'],
                         ('tasks', 'payments'))
        self.assertEqual(self._viewer(229, 'admin', can_see_tasks=True, payments_open=False)['hidden_sources'],
                         ('payments',))
        named = self._viewer(ANALYST_ID, 'operator', payments_open=False)['hidden_sources']
        self.assertEqual(named.count('payments'), 1)
        # Настоящее правило — периметр раздела, а не роль.
        namespace = {'logging': logging}
        exec(_function_source('_payments_section_open_for'), namespace)
        from payments import access as payments_access
        opened = namespace['_payments_section_open_for']
        self.assertTrue(opened(payments_access.SECTION_ALLOWED_USER_IDS[0]))
        self.assertFalse(opened(999999))
        self.assertFalse(opened(None))

    def test_bell_of_everyone_else_is_untouched(self):
        # Сосед по отделу — оператор без личного набора: скрыты только «Задачи».
        self.assertEqual(self._viewer(ANALYST_ID + 1, 'operator')['hidden_sources'], ('tasks',))
        self.assertEqual(self._viewer(229, 'admin', can_see_tasks=True, heads={229})['hidden_sources'], ())
        # Тот же id в роли не рядового или во главе отдела — без набора: у
        # него меню своей роли, и колокол зовёт в её разделы.
        for role in ('sv', 'trainer', 'admin', 'super_admin'):
            self.assertEqual(self._viewer(ANALYST_ID, role, can_see_tasks=True)['hidden_sources'], (), role)
        self.assertEqual(self._viewer(ANALYST_ID, 'operator', can_see_tasks=True,
                                      heads={ANALYST_ID})['hidden_sources'], ())


class FrontendWiringTests(unittest.TestCase):
    """Три раздела, которые карта отдела не ведёт, обязаны спрашивать набор
    сами — иначе у человека с личным набором они останутся в меню."""

    @classmethod
    def setUpClass(cls):
        cls.app = _read(APP_JSX)

    def test_wiki_library_and_events_ask_the_set(self):
        self.assertIn("const wikiSectionEnabled = wikiEnabledFor(user) && personalViewsAllow(user, 'wiki');",
                      self.app)
        self.assertIn("const canAccessLibrarySection = canAccessLibrarySectionForUser(user)\n"
                      "                && personalViewsAllow(user, 'library');", self.app)
        self.assertIn("const eventsSectionShown = departmentAllowsView(user, 'events');", self.app)
        # Флаги одни на всё: ни пункт меню, ни экран, ни гард не считают своих.
        self.assertEqual(self.app.count('wikiEnabledFor(user)'), 1)
        self.assertEqual(self.app.count('canAccessLibrarySectionForUser(user)'), 1)

    def test_events_item_and_its_divider_follow_the_flag(self):
        self.assertEqual(self.app.count('renderEventsSidebarItemInner()'), 1)
        self.assertIn('{renderDividerIfInner(eventsSectionShown, canAccessDevLetterSection)}\n'
                      '                                    {eventsSectionShown && renderEventsSidebarItemInner()}',
                      self.app)
        # Пункт живёт внутри sidebarTree = useMemo(...): без зависимости он не
        # исчез бы после смены пользователя.
        deps_start = self.app.index('}, [', self.app.index('{eventsSectionShown && renderEventsSidebarItemInner()}'))
        self.assertIn('eventsSectionShown', self.app[deps_start:self.app.index(']);', deps_start)])

    def test_every_consumer_reads_the_flags_not_the_raw_predicates(self):
        """Флаги «Вики» и «Библиотеки» спрашивают набор один раз, а дальше их
        читают все: пункт меню, экран, бар телефона и плавающий помощник. Место,
        которое пошло бы мимо флага к сырому признаку, показало бы раздел
        человеку с набором."""
        # Пункты меню — под флагами.
        self.assertIn("{wikiSectionEnabled && (\n"
                      "                                    <li>\n"
                      "                                        <button\n"
                      '                                            type="button"\n'
                      "                                            onClick={(e) => handleSidebarViewNavigation(e, 'wiki')}",
                      self.app)
        self.assertIn("{canAccessLibrarySection && (\n"
                      "                                    <li>\n"
                      "                                        <button\n"
                      '                                            type="button"\n'
                      "                                            onClick={(e) => handleSidebarViewNavigation(e, 'library')}",
                      self.app)
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'wiki')"), 1)
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'library')"), 1)
        # Экран «Библиотеки», бар телефона и помощник.
        self.assertIn("{view === 'library' && canAccessLibrarySection && (", self.app)
        self.assertIn('                    wiki: wikiSectionEnabled,\n', self.app)
        self.assertEqual(self.app.count('wikiEnabled={wikiSectionEnabled}'), 1)
        self.assertEqual(self.app.count('wikiEnabled={'), 1)
        # Сырой признак профиля читает только сам флаг.
        self.assertEqual(self.app.count('user?.wiki_enabled'), 1)

    def test_guard_leads_away_from_hidden_sections(self):
        """Гард «Этап 10» уводит из раздела вне набора: флаги «Вики» и
        «Библиотеки» у человека с набором ложны, «Ивенты» спрашивают карту."""
        start = self.app.index('// Гард видимости разделов по отделу (Этап 10)')
        guard = self.app[start:self.app.index('wikiSectionEnabled, view]);', start)]
        self.assertIn('if (!departmentRestrictsViews(user)) return;', guard)
        self.assertIn("if (view === 'wiki' && wikiSectionEnabled) return;", guard)
        self.assertIn("if (view === 'library' && canAccessLibrarySection) return;", guard)
        self.assertIn("if (view === 'baiga' && canAccessBaigaSection) return;", guard)
        tail = guard[guard.index("if (view === 'library' && canAccessLibrarySection) return;"):]
        self.assertIn("                if (departmentAllowsView(user, view)) return;\n"
                      "                // Перенаправляем на первый разрешённый раздел роли "
                      "(для sv это manage_operators, для оператора — salary).\n"
                      "                const fallback = firstAllowedView(user, []) || 'salary';\n"
                      "                if (fallback && fallback !== view) redirectToView(fallback);\n", tail)
        # «Ивентов» среди собственных предикатов гарда нет: их решает карта.
        self.assertNotIn("view === 'events'", guard)
        # Гард пересчитывается, когда меняется то, от чего зависит набор (id,
        # роль, главенство) и флаги, которые набор гасит.
        deps = self.app[self.app.index('wikiSectionEnabled, view]);', start) - 1400:
                        self.app.index('wikiSectionEnabled, view]);', start) + 30]
        deps = deps[deps.rindex('}, ['):]
        for name in ('user?.id', 'user?.role', 'user?.headed_department_id', 'canAccessBaigaSection',
                     'canAccessLibrarySection', 'wikiSectionEnabled', 'view'):
            self.assertRegex(deps, r'[\[ ]%s[,\]]' % re.escape(name), name)

    def test_person_sent_to_baiga_by_the_set_is_not_bounced_out_of_it(self):
        """Набор назвал раздел, а самого допуска нет (выключатель пилота, строка
        забыта в списке аналитиков): гард «Этап 10» ведёт человека в «Списки
        Байги», а страж раздела уводил бы оттуда в «Мои часы» — и так без конца,
        вкладка зависает. Страж обязан оставить его на месте."""
        effect = self.app.split("if (view === 'baiga' && !canAccessBaigaSection) {")[1]
        branch = effect.split('\n                }\n', 1)[0]
        self.assertIn("const sentHereByViewMap = departmentRestrictsViews(user) "
                      "&& departmentAllowsView(user, 'baiga');", branch)
        guard_at = branch.index('if (!sentHereByViewMap) {')
        # Ни одного переброса до проверки и все — под ней.
        self.assertNotIn('redirectToView(', branch[:guard_at])
        for target in ('sv_list', 'operators', 'surveys', 'hours'):
            self.assertIn("redirectToView('%s');" % target, branch[guard_at:])
        # Сама проверка — та же пара, которой гард «Этап 10» решает, оставить ли
        # человека в разделе: правило одно, поэтому и разойтись им негде.
        start = self.app.index('// Гард видимости разделов по отделу (Этап 10)')
        guard = self.app[start:self.app.index('wikiSectionEnabled, view]);', start)]
        self.assertIn('if (!departmentRestrictsViews(user)) return;', guard)
        self.assertIn('if (departmentAllowsView(user, view)) return;', guard)

    def test_login_needs_no_branch_of_its_own(self):
        """Вход ведёт человека в «Мои часы», как любого рядового, а в его раздел
        уводит тот же гард «Этап 10» в том же проходе эффектов — так же, как
        сотрудника ООЗ в «Рассылки». Своей ветки входа у набора нет намеренно:
        вторая дорога в тот же раздел однажды разошлась бы с первой."""
        start = self.app.index('// Persist and restore view (after user is loaded')
        effect = self.app[start:self.app.index('requestedViewFromLocation]);', start)]
        self.assertNotIn('personalViews', effect)
        self.assertIn("else redirectToView('hours');", effect)
        # Гард объявлен ПОСЛЕ входа: эффекты одного прохода идут по порядку.
        self.assertLess(start, self.app.index('// Гард видимости разделов по отделу (Этап 10)'))

    def test_helpers_are_imported(self):
        line = next(row for row in self.app.splitlines() if "from './utils/departmentViews';" in row)
        for name in ('personalViewsAllow', 'departmentAllowsView', 'firstAllowedView'):
            self.assertRegex(line, r'\b%s\b' % name)


if __name__ == '__main__':
    unittest.main()
