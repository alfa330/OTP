# -*- coding: utf-8 -*-
"""Проводка раздела «Учёт воды» и пары «фронт ↔ сервер», которые обязаны совпадать.

  * «Учёт воды» — свой раздел меню (решение владельца 30.09.2026), за тем же
    QR-замком, что «Посылки»; «Посылки» при этом не тронуты;
  * подписи тарифов и видов выдачи на экране и в файле — одни и те же;
  * схема разворачивается при старте, Blueprint подключён со всеми зависимостями;
  * DDL держит главное: остаток не уходит в минус, приветственный — один.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from water import access, notify, report, rules, schema

ROOT = Path(__file__).resolve().parents[1]
PARCELS_VIEW = ROOT / 'src' / 'components' / 'parcels' / 'ParcelsView.jsx'
APP_JSX = ROOT / 'src' / 'App.jsx'
META = ROOT / 'src' / 'components' / 'water' / 'waterMeta.js'
PANEL = ROOT / 'src' / 'components' / 'water' / 'WaterPanel.jsx'


def _read(path):
    return path.read_text(encoding='utf-8-sig')


def _js_object(source, name):
    block = source.split('export const %s = {' % name)[1].split('};')[0]
    return dict(re.findall(r"(\w+):\s*'([^']*)'", block))


class SectionWiringTests(unittest.TestCase):
    """Свой раздел меню, а не вкладка «Посылок» (решение владельца 30.09.2026)."""

    def setUp(self):
        self.app = _read(APP_JSX)

    def test_parcels_are_untouched(self):
        view = _read(PARCELS_VIEW)
        self.assertIn('export default ParcelsView;', view)
        self.assertNotIn('water', view.lower().replace('waterfall', ''))

    def test_lazy_import_and_render_behind_the_qr_gate(self):
        self.assertIn("const WaterView = lazyWithRetry(() => import('./components/water/WaterView'));", self.app)
        block = self.app.split('{view === "water" && canAccessWaterSection && (sensitiveSectionsLocked ? (')[1]
        block = block.split('))}')[0]
        self.assertIn('<SensitiveSectionGate', block)
        self.assertIn('<WaterView', block)

    def test_menu_item_is_declared_once_in_the_common_part(self):
        """Как у «Посылок»: пункт один, в общей части меню, — главам отделов он
        виден без дубля в ролевых ветках (см. sidebar-item-must-be-in-two-branches)."""
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'water')"), 1)
        item = self.app.split("handleSidebarViewNavigation(e, 'water')")[1][:600]
        self.assertIn('fas fa-droplet', item)
        self.assertIn('Учёт воды', item)
        parcels_item = self.app.index("handleSidebarViewNavigation(e, 'parcels')")
        self.assertLess(parcels_item, self.app.index("handleSidebarViewNavigation(e, 'water')"))

    def _predicate(self):
        return self.app.split('const canAccessWaterSectionForUser = (userLike) => {')[1].split('\n};')[0]

    def test_issuer_list_is_the_same_on_both_sides(self):
        """Пункт меню и сервер обязаны совпадать — иначе человек видит пункт, а
        раздел отвечает отказом (или наоборот: доступ выдан, а пункта нет)."""
        ids = re.search(r'const WATER_ISSUER_USER_IDS = new Set\(\[([0-9,\s]*)\]\);', self.app)
        self.assertIsNotNone(ids)
        front = {int(x) for x in ids.group(1).split(',') if x.strip()}
        self.assertEqual(front, set(access.ISSUER_USER_IDS))
        self.assertNotIn('WATER_PILOT', self.app)

    def test_predicate_checks_in_the_backend_order(self):
        """Супер-админ → ТЭЗ закрыт → руководитель → офисники по списку → СЗоВ,
        как в water.access.can_open_section: ТЭЗ закрыт раньше любых оснований."""
        predicate = self._predicate()
        steps = ["=== 'super_admin') return true;",
                 "if (own === 'tez' || headed.includes('tez')) return false;",
                 "if (isDepartmentHead(userLike) && headed.includes('front_office')) return true;",
                 "if (own === 'front_office') return WATER_ISSUER_USER_IDS.has(Number(userLike?.id));",
                 "return own === 'szov' || headed.includes('szov');"]
        positions = [predicate.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(access.CLOSED_DEPARTMENT_CODES, ('tez',))
        self.assertEqual((access.ISSUE_DEPARTMENT_CODE, access.CHECK_DEPARTMENT_CODE),
                         ('front_office', 'szov'))

    def test_predicate_answers_like_the_server(self):
        """Сам предикат из App.jsx, выполненный node, против can_open_section на
        всех сочетаниях роли, отдела, главенства и списка: проверяем поведение,
        а не текст."""
        node = shutil.which('node')
        if not node:
            self.skipTest('node недоступен')
        app = self.app
        normalize = re.search(r'^const normalizeDepartmentCode = .*$', app, re.M).group(0)
        start = app.index('const aiQaHeadDepartmentCodesOf = (userLike) => {')
        head_codes = app[start:app.index('\n};\n', start) + 3]
        start = app.index('const WATER_ISSUER_USER_IDS = ')
        end = app.index('\n};\n', app.index('const canAccessWaterSectionForUser = ')) + 3
        cases = []
        for role in ('super_admin', 'admin', 'sv', 'trainer', 'operator', 'trainee'):
            for code in ('front_office', 'szov', 'tez', 'op', 'it', None):
                for heads in ((), ('front_office',), ('szov',), ('tez',), ('op',)):
                    for user_id in (10, 424):
                        cases.append((role, code, heads, user_id))
        users = [{'id': user_id, 'role': role, 'department_code': code,
                  'headed_department_id': 1 if heads else None,
                  'headed_department_codes': list(heads)}
                 for role, code, heads, user_id in cases]
        roles_url = (ROOT / 'src' / 'utils' / 'roles.js').as_uri()
        script = '\n'.join((
            "import { normalizeRole, isDepartmentHead } from '%s';" % roles_url,
            normalize, head_codes, app[start:end],
            'const users = %s;' % json.dumps(users),
            'process.stdout.write(JSON.stringify(users.map((u) => canAccessWaterSectionForUser(u))));',
        ))
        out = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True, check=True)
        front = json.loads(out.stdout.decode('utf-8'))
        for (role, code, heads, user_id), shown in zip(cases, front):
            ctx = {'user_id': user_id, 'role': role, 'department_code': code,
                   'headed_department_ids': [1] if heads else [],
                   'headed_department_codes': list(heads)}
            self.assertEqual(shown, access.can_open_section(ctx), (role, code, heads, user_id))
        self.assertEqual(len(front), len(cases))

    def test_guards_and_registries(self):
        self.assertIn("if (view === 'water' && canAccessWaterSection) return;", self.app)
        self.assertIn("    water: ['front_office', 'szov'],", self.app)
        self.assertIn("    water: 'Water accounting',", self.app)
        self.assertIn("|| view === 'water' || view === 'driver_chats'", self.app)
        self.assertIn("canAccessWaterSection && deptAllowsInner('water'),", self.app)
        # Тренер — как операторы своего отдела (01.10.2026): раздел в его списке,
        # а исключения по роли в предикате нет.
        trainer = self.app.split('const TRAINER_ALLOWED_VIEWS = Object.freeze([')[1].split(']);')[0]
        self.assertIn("'water'", trainer)
        self.assertNotIn("'trainer'", self._predicate())

    def test_droplet_icon_is_mapped(self):
        fa = _read(ROOT / 'src' / 'components' / 'common' / 'FaIcon.jsx')
        self.assertIn("'fa-droplet': 'Droplet',", fa)

    def test_telegram_link_opens_the_section(self):
        self.assertEqual(notify.section_link('https://x.kz/OTP/'), 'https://x.kz/OTP/?view=water')


class LabelMirrorTests(unittest.TestCase):
    def setUp(self):
        self.meta = _read(META)

    def test_tariff_labels_match_the_server(self):
        self.assertEqual(_js_object(self.meta, 'TARIFF_LABELS'), rules.TARIFF_LABELS)

    def test_kind_labels_match_the_workbook(self):
        self.assertEqual(_js_object(self.meta, 'KIND_LABELS'), report.KIND_LABELS)

    def test_export_ceiling_matches(self):
        self.assertIn('EXPORT_MAX_DAYS = %d;' % report.EXPORT_MAX_DAYS, self.meta)

    def test_default_program_tariffs_are_business_and_premier(self):
        self.assertEqual(schema.DEFAULT_TARIFFS, ('business', 'ultimate'))
        self.assertIn("""'["business", "ultimate"]'""", ' '.join(schema._STATEMENTS))


class PanelTests(unittest.TestCase):
    """Вкладки по правам (01.10.2026): журнал и настройки — руководителю,
    остальным вместо настроек — «Условия» для чтения, колл-центру — без выдачи."""

    def setUp(self):
        self.panel = _read(PANEL)

    def test_journal_tab_and_screen_only_with_the_journal_right(self):
        self.assertIn("capabilities.can_view_journal ? { value: 'journal', label: 'Журнал' } : null",
                      self.panel)
        self.assertIn("activeTab === 'journal' && capabilities.can_view_journal && (", self.panel)

    def test_settings_for_the_manager_conditions_for_everyone_else(self):
        self.assertIn("{ value: 'settings', label: manage ? 'Настройки' : 'Условия' }", self.panel)
        self.assertIn("activeTab === 'settings' && capabilities.can_manage && (\n                <WaterSettings",
                      self.panel)
        self.assertIn("activeTab === 'settings' && !capabilities.can_manage && (\n"
                      "                <WaterConditions settings={settings} />", self.panel)

    def test_call_centre_tabs_lead_with_stock_and_conditions(self):
        tabs = self.panel.split('const tabs = useMemo(() => {')[1].split('}, [capabilities]);')[0]
        viewer = tabs.split('return [')[-1].split('];')[0]
        self.assertEqual(re.findall(r"label: '([^']+)'", viewer), ['Остатки', 'Условия', 'Проверка'])
        self.assertNotIn('journal', viewer)
        # Первая вкладка — первая в наборе роли, а не «Выдача» для всех.
        self.assertIn('const [tab, setTab] = useState(null);', self.panel)
        self.assertIn('tabs.some((item) => item.value === tab) ? tab : tabs[0]?.value', self.panel)

    def test_add_office_placeholder_names_the_program_cities(self):
        """Пустой справочник «Офис в учёт» объясняет, почему пуст, — теми же
        городами, что держит сервер (rules.PROGRAM_CITIES)."""
        stock = _read(ROOT / 'src' / 'components' / 'water' / 'WaterStock.jsx')
        self.assertEqual(rules.PROGRAM_CITIES, ('Алматы', 'Астана'))
        self.assertIn("'Все офисы Алматы и Астаны уже в учёте'", stock)

    def test_quantity_fields_take_digits_only(self):
        """«Поля, где нужно вводить количество, — только числа, а не буквы»
        (01.10.2026): все поля количества идут через CountInput, а он ввод с
        нецифрой отбрасывает (не вычищает: из «12,5» вышло бы 125)."""
        water = ROOT / 'src' / 'components' / 'water'
        for path in water.glob('*.jsx'):
            if path.name != 'CountInput.jsx':
                self.assertNotIn('inputMode="numeric"', _read(path), path.name)
        stock = _read(water / 'WaterStock.jsx')
        self.assertEqual(stock.count('<CountInput '), 4)  # поступление/пересчёт, два порога, офис в учёт
        settings = _read(water / 'WaterSettings.jsx')
        self.assertIn('const { rejected, inputProps } = useCountInput(onChange);', settings)
        self.assertIn('<input className={NUMBER_INPUT} {...inputProps} value={value} />', settings)
        count = _read(water / 'CountInput.jsx')
        handler = count.split('const handleChange = (event) => {')[1].split('\n    };')[0]
        self.assertLess(handler.index('if (!isCountDraft(next)) {'), handler.index('onChange(next);'))
        self.assertIn('setRejected(true);\n            return;', handler)
        self.assertNotIn('replace(', handler)

    def test_stock_shows_the_office_address(self):
        """«Отображать адрес самого офиса, не только название» (01.10.2026):
        одно название «Бизнес» есть и в Алматы, и в Астане."""
        stock = _read(ROOT / 'src' / 'components' / 'water' / 'WaterStock.jsx')
        self.assertEqual(stock.count('{officePlace(row)}'), 2)  # таблица и карточки на телефоне
        self.assertIn('subtitle={officePlace(office)}', stock)

    def test_no_native_selects_or_date_inputs(self):
        """Эталон портала — свои пикеры: системный select/date — чужая деталь."""
        for path in (ROOT / 'src' / 'components' / 'water').glob('*.jsx'):
            source = _read(path)
            self.assertNotIn('<select', source, path.name)
            self.assertNotIn('type="date"', source, path.name)


class JournalCancelTests(unittest.TestCase):
    """Отмену видит и делает только руководитель; ФИО во Флит не открывает
    заодно карточку выдачи."""

    def setUp(self):
        self.journal = _read(ROOT / 'src' / 'components' / 'water' / 'WaterJournal.jsx')
        self.panel = _read(PANEL)

    def test_only_the_manager_opens_the_issue_sheet(self):
        self.assertIn('onClick={canManage ? () => setOpened(item) : undefined}', self.journal)
        self.assertIn('{canManage && (\n                <IssueSheet', self.journal)
        self.assertIn('canManage={Boolean(capabilities.can_manage)}', self.panel)

    def test_cancel_needs_a_reason_before_the_button_wakes_up(self):
        self.assertIn('disabled={!reason.trim() || saving}', self.journal)
        self.assertIn('/api/water/issues/${item.id}/cancel', self.journal)

    def test_links_inside_a_clickable_row_stop_propagation(self):
        driver_name = self.journal.split('const DriverName = ')[1].split('};')[0]
        self.assertIn('event.stopPropagation()', driver_name)


class WiringTests(unittest.TestCase):
    def test_schema_is_initialised_on_start(self):
        database = _read(ROOT / 'database.py')
        self.assertIn('self._init_water_schema_tx(cursor)', database)
        self.assertIn('from water.schema import init_water_schema', database)
        # После вики: office_id — внешний ключ на wiki_offices.
        self.assertLess(database.index('self._init_wiki_schema_tx(cursor)'),
                        database.index('self._init_water_schema_tx(cursor)'))

    def test_blueprint_is_registered_with_its_dependencies(self):
        bot = _read(ROOT / 'bot_schedule2.py')
        block = bot.split('build_water_blueprint(\n')[1].split('))')[0]
        for name in ('sensitive_access_granted=_sensitive_access_granted_for_user',
                     'excel_text_warning=_excel_suppress_number_as_text_warning',
                     'send_telegram=_send_telegram_text_message',
                     'web_app_base_url=TASK_WEB_APP_BASE_URL'):
            self.assertIn(name, block)


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.ddl = '\n'.join(schema._STATEMENTS)

    def test_stock_never_goes_negative(self):
        self.assertIn('stock           INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0)', self.ddl)

    def test_welcome_is_unique_per_account_and_per_person_among_live_issues(self):
        self.assertIn("uq_water_welcome_account_live "
                      "ON water_issues(driver_account_id) WHERE kind = 'welcome' AND canceled_at IS NULL",
                      self.ddl)
        self.assertIn("uq_water_welcome_iin_live "
                      "ON water_issues(driver_iin) WHERE kind = 'welcome' AND driver_iin IS NOT NULL "
                      "AND canceled_at IS NULL", self.ddl)

    def test_old_welcome_indexes_are_dropped_on_live_bases(self):
        """Старое условие считало и отменённые — повторная выдача после отмены
        упиралась бы в него; CREATE IF NOT EXISTS по старому имени его не сменил бы."""
        migrations = '\n'.join(schema._MIGRATIONS)
        self.assertIn('DROP INDEX IF EXISTS uq_water_welcome_account', migrations)
        self.assertIn('DROP INDEX IF EXISTS uq_water_welcome_iin', migrations)
        self.assertNotIn('uq_water_welcome_account "', self.ddl)
        for column in ('canceled_at', 'canceled_by', 'canceled_by_name', 'cancel_reason'):
            self.assertIn('ADD COLUMN IF NOT EXISTS %s' % column, migrations)

    def test_tables_before_indexes(self):
        executed = []

        class Cursor:
            def execute(self, statement, params=None):
                executed.append(statement)

        schema.init_water_schema(Cursor())
        tables = [i for i, s in enumerate(executed) if 'CREATE TABLE' in s.upper()]
        others = [i for i, s in enumerate(executed) if 'CREATE TABLE' not in s.upper()]
        self.assertLess(max(tables), min(others))


if __name__ == '__main__':
    unittest.main()
