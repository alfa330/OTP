# -*- coding: utf-8 -*-
"""Проводка раздела «Учёт воды» и пары «фронт ↔ сервер», которые обязаны совпадать.

  * «Учёт воды» — свой раздел меню (решение владельца 30.09.2026), за тем же
    QR-замком, что «Посылки»; «Посылки» при этом не тронуты;
  * подписи тарифов и видов выдачи на экране и в файле — одни и те же;
  * схема разворачивается при старте, Blueprint подключён со всеми зависимостями;
  * DDL держит главное: остаток не уходит в минус, приветственный — один.
"""

import re
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

    def test_predicate_mirrors_the_backend_perimeter(self):
        self.assertIn("const WATER_SECTION_DEPARTMENT_CODES = ['front_office', 'szov'];", self.app)
        self.assertEqual(set(access.SECTION_DEPARTMENT_CODES), {'front_office', 'szov'})
        predicate = self.app.split('const canAccessWaterSectionForUser = (userLike) => {')[1].split('\n};')[0]
        self.assertIn("role === 'admin' && !isDepartmentHead(userLike)", predicate)

    def test_guards_and_registries(self):
        self.assertIn("if (view === 'water' && canAccessWaterSection) return;", self.app)
        self.assertIn("    water: ['front_office', 'szov'],", self.app)
        self.assertIn("    water: 'Water accounting',", self.app)
        self.assertIn("|| view === 'water' || view === 'driver_chats'", self.app)
        self.assertIn("canAccessWaterSection && deptAllowsInner('water'),", self.app)
        # Тренер раздел не просил: ни в его списке, ни в предикате.
        trainer = self.app.split('const TRAINER_ALLOWED_VIEWS = Object.freeze([')[1].split(']);')[0]
        self.assertNotIn("'water'", trainer)
        predicate = self.app.split('const canAccessWaterSectionForUser = (userLike) => {')[1].split('\n};')[0]
        self.assertIn("if (role === 'trainer') return false;", predicate)

    def test_pilot_flag_is_the_same_on_both_sides(self):
        """Пилот «только супер-админ»: пункт меню и сервер снимаются вместе."""
        flag = re.search(r'const WATER_PILOT_SUPER_ADMIN_ONLY = (true|false);', self.app)
        self.assertIsNotNone(flag)
        self.assertEqual(flag.group(1) == 'true', access.PILOT_SUPER_ADMIN_ONLY)
        predicate = self.app.split('const canAccessWaterSectionForUser = (userLike) => {')[1].split('\n};')[0]
        self.assertLess(predicate.index("if (role === 'super_admin') return true;"),
                        predicate.index('if (WATER_PILOT_SUPER_ADMIN_ONLY) return false;'))

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
    def test_settings_tab_only_for_the_manager(self):
        panel = _read(PANEL)
        self.assertIn("capabilities?.can_manage ? { value: 'settings'", panel)
        self.assertIn("tab === 'settings' && capabilities.can_manage", panel)

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
