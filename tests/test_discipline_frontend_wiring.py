# -*- coding: utf-8 -*-
"""«Дисциплина» (#254): раздел подключён к порталу во всех местах, а зеркала
правил на клиенте совпадают с сервером.

Забытая строка регистрации не роняет сборку — она молча прячет раздел
(редирект гарда «Этап 10», пропавший пункт меню, неоткрывающаяся ссылка из
Telegram). Поэтому каждое место проверяется здесь явно.
"""

import json
import re
import subprocess
import unittest
from pathlib import Path

from discipline import access, catalog
from notifications import sources

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
BELL = (ROOT / 'src' / 'components' / 'notifications' / 'NotificationsBell.jsx').read_text(encoding='utf-8')
RULES = ROOT / 'src' / 'components' / 'discipline' / 'disciplineRules.js'


def _js_object(name):
    """Значение экспортированного объекта disciplineRules.js — через node."""
    script = "import(%s).then((m) => console.log(JSON.stringify(m[%s])))" % (
        json.dumps(RULES.as_uri()), json.dumps(name))
    out = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True,
                         check=True, timeout=30)
    return json.loads(out.stdout)


class AppWiringTests(unittest.TestCase):
    def test_lazy_view_and_render_behind_qr_lock(self):
        self.assertIn("const DisciplineView = lazyWithRetry(() => import('./components/discipline/DisciplineView'));", APP)
        self.assertIn('{view === "discipline" && canAccessDisciplineSection && (sensitiveSectionsLocked ? (', APP)
        self.assertIn('focusRequest={disciplineFocusRequest}', APP)

    def test_guard_url_and_sidebar(self):
        self.assertIn("if (view === 'discipline' && canAccessDisciplineSection) return;", APP)
        self.assertIn("(requestedViewFromUrl !== 'discipline' || canAccessDisciplineSection)", APP)
        self.assertEqual(APP.count("handleSidebarViewNavigation(e, 'discipline')"), 1)
        self.assertIn("showDisciplineMenu && deptAllowsInner('discipline')", APP)
        self.assertIn("discipline: 'Discipline',", APP)
        # Зависимости эффектов и мемо меню: без них признак застывает на первом рендере.
        self.assertIn('canAccessWaterSection, canAccessDisciplineSection, canAccessThermoboxesSection', APP)
        self.assertIn('canAccessLibrarySection, canAccessDisciplineSection, requestedViewFromLocation]', APP)
        self.assertIn('canAccessWaterSection,\n                showDisciplineMenu,', APP)
        self.assertIn("|| view === 'discipline' || view === 'baiga' || view === 'driver_mailings') {", APP)

    def test_department_lists_match_server(self):
        match = re.search(r"const DISCIPLINE_DEPARTMENT_CODES = \[([^\]]*)\];", APP)
        self.assertTrue(match)
        codes = tuple(re.findall(r"'([a-z_]+)'", match.group(1)))
        self.assertEqual(codes, catalog.SECTION_DEPARTMENT_CODES)
        self.assertIn("discipline: ['szov', 'op', 'tez'],", APP)

    def test_rollout_flag_matches_server(self):
        # Пока раздел открыт одним супер-админам — и в меню, и на сервере.
        match = re.search(r"const DISCIPLINE_SUPER_ADMIN_ONLY = (true|false);", APP)
        self.assertTrue(match)
        self.assertEqual(match.group(1) == 'true', access.SUPER_ADMIN_ONLY)
        body = APP[APP.index('const canAccessDisciplineSectionForUser'):APP.index('const isDisciplineOperatorUser')]
        self.assertLess(body.index("if (role === 'super_admin') return true;"),
                        body.index('if (DISCIPLINE_SUPER_ADMIN_ONLY) return false;'))
        self.assertLess(body.index('if (DISCIPLINE_SUPER_ADMIN_ONLY) return false;'), body.index('isDepartmentHead(userLike)'))

    def test_admin_without_heading_is_out(self):
        body = APP[APP.index('const canAccessDisciplineSectionForUser'):APP.index('const isDisciplineOperatorUser')]
        # Порядок важен: глава отдела раздела проходит раньше, чем админ отсекается.
        self.assertLess(body.index('isDepartmentHead(userLike)'), body.index("if (role === 'admin') return false;"))

    def test_qr_rule_matches_portal_lock(self):
        # Сервер просит QR у роли «оператор» — общий замок портала тоже.
        self.assertIn("const SENSITIVE_QR_GATED_ROLES = new Set(['operator',", APP)

    def test_bell(self):
        self.assertIn('discipline', sources.SOURCES)
        self.assertEqual(sources.SOURCE_VIEWS['discipline'], ('discipline',))
        self.assertIn('discipline', sources.OWN_CIRCLE_VIEWS)
        self.assertIn("discipline: { label: 'Дисциплина'", BELL)
        self.assertIn("if (nextView === 'discipline' && target) {", APP)


class MirrorTests(unittest.TestCase):
    def test_state_labels_and_tones(self):
        self.assertEqual(_js_object('STATE_LABELS'), dict(catalog.STATE_LABELS))
        self.assertEqual(_js_object('STATE_TONES'), dict(catalog.STATE_TONES))

    def test_step_labels(self):
        self.assertEqual(_js_object('STEP_LABELS'), dict(catalog.STEP_LABELS))

    def test_description_minimum(self):
        self.assertEqual(_js_object('DESCRIPTION_MIN'), catalog.DESCRIPTION_MIN_CHARS)

    def test_file_limits(self):
        # Размер и число файлов клиент проверяет до отправки — по тем же пределам.
        limits = _js_object('FILE_LIMITS')
        self.assertEqual(set(limits), set(catalog.FILE_KINDS))
        for kind, spec in catalog.FILE_KINDS.items():
            self.assertEqual(limits[kind]['maxBytes'], spec['max_bytes'], kind)
            self.assertEqual(limits[kind]['maxFiles'], spec['max_files'], kind)
            self.assertEqual(limits[kind].get('videoMaxBytes'), spec.get('video_max_bytes'), kind)

    def test_states_beside_step_are_known(self):
        for name in ('CARD_STATES_BESIDE_STEP', 'ROW_STATES_BESIDE_STEP'):
            self.assertLessEqual(set(_js_object(name)), set(catalog.STATE_LABELS), name)


class StackAndDraftTests(unittest.TestCase):
    VIEW = (ROOT / 'src' / 'components' / 'discipline' / 'DisciplineView.jsx').read_text(encoding='utf-8')
    FORM = (ROOT / 'src' / 'components' / 'discipline' / 'RecordForm.jsx').read_text(encoding='utf-8')

    def test_one_window_for_the_stack(self):
        # Одно окно на стопку: жест «назад» на телефоне снимает один уровень, как
        # шеврон, и возвращает false — запись окна в истории остаётся для
        # следующего жеста (useScreenBackGesture: false = «остаюсь»).
        self.assertEqual(self.VIEW.count('<IosModal'), 1)
        self.assertIn('const stepBack = onBack ? () => { onBack(); return false; } : null;', self.VIEW)
        self.assertIn('onClose={mobile && stepBack ? stepBack : onClose}', self.VIEW)

    def test_bell_focus_is_reset_when_leaving(self):
        self.assertIn("if (view !== 'discipline') setDisciplineFocusRequest(null);", APP)

    def test_drafts_are_per_user_and_cleared_on_logout(self):
        self.assertIn('readDrafts(userId)', self.FORM)
        self.assertIn('userId={ping?.user_id}', self.VIEW)
        self.assertIn(".filter((key) => key.startsWith('otp.discipline.draft.'))", APP)

    def test_section_root_paints_page_on_phone(self):
        # MobilePageChrome красит страницу на телефоне цветом корня раздела;
        # прозрачный корень открывал логотип с фона портала под карточками.
        root = re.search(r'return \(\s*<div className="(mx-auto w-full max-w-\[1180px\][^"]*)"', self.VIEW)
        self.assertIsNotNone(root)
        self.assertIn('bg-gray-50', root.group(1).split())



class TimeFieldsTests(unittest.TestCase):
    """Дата, смена и тайм-код — пикерами портала (владелец, 20.10.2026: «сделай
    кастомный пикер», «чтобы просто вводилось время, а не рандомные числа»)."""
    DIR = ROOT / 'src' / 'components' / 'discipline'

    def test_no_system_date_or_time_fields_in_section(self):
        for path in sorted(self.DIR.glob('*.jsx')):
            text = path.read_text(encoding='utf-8')
            for native in ('type="datetime-local"', 'type="date"', 'type="time"'):
                self.assertNotIn(native, text, path.name)

    def test_both_forms_use_time_fields(self):
        form = (self.DIR / 'RecordForm.jsx').read_text(encoding='utf-8')
        sheet = (self.DIR / 'RecordSheet.jsx').read_text(encoding='utf-8')
        for text in (form, sheet):
            for field in ('<MomentField', '<ShiftField', '<TimecodeField'):
                self.assertIn(field, text)
        # Ответ сервера о формате — к своему полю.
        self.assertIn("SHIFT_FORMAT: 'shift_label'", form)
        self.assertIn("EVIDENCE_NOTE: 'evidence'", form)
        self.assertIn("SHIFT_FORMAT: 'shift_label', EVIDENCE_NOTE: 'evidence_note'", sheet)

    def test_time_fields_use_portal_pickers(self):
        fields = (self.DIR / 'TimeFields.jsx').read_text(encoding='utf-8')
        self.assertIn("import IosDatePicker from '../ui/DatePicker';", fields)
        self.assertIn("import { IosTimePicker } from '../ui/TimePicker';", fields)
        # Обёртка с красной рамкой — всегда одна и та же: условная пересоздала бы поле.
        self.assertIn("const ring = (on) => `rounded-xl ${on ? INVALID_RING : ''}`;", fields)

    def test_empty_time_panel_opens_at_default_time(self):
        # Пустой тайм-код: панель часов открывается у времени нарушения, а не на 00.
        picker = (ROOT / 'src' / 'components' / 'ui' / 'TimePicker.jsx').read_text(encoding='utf-8')
        self.assertIn("col?.querySelector('[data-active=\"1\"]') || col?.querySelector('[data-anchor=\"1\"]')", picker)
        self.assertIn("data-anchor={col.isAnchor(item) ? '1' : undefined}", picker)



class SearchFieldTests(unittest.TestCase):
    """Владелец, 22.10.2026, скриншот формы нарушения на большом экране: лупа
    лежала на первых буквах «ФИО или логин» (2xl:px-4 из dInput перебивал
    pl-9), а окно прыгало, пока приходил и менялся список сотрудников.
    Поведение списка — tests/discipline_operator_picker_render.test.mjs."""
    DIR = ROOT / 'src' / 'components' / 'discipline'

    def test_every_search_goes_through_search_field(self):
        for path in sorted(self.DIR.glob('*.jsx')):
            if path.name == 'disciplineUi.jsx':
                continue
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('<Search ', text, path.name)
            self.assertNotRegex(text, r'\$\{dInput\} pl-\d', path.name)

    def test_operator_pickers_share_one_component(self):
        for name in ('RecordForm.jsx', 'ControlsPanel.jsx'):
            text = (self.DIR / name).read_text(encoding='utf-8')
            self.assertIn('<OperatorPicker', text, name)
            self.assertIn('useOperatorSearch(api, open, !', text, name)
            self.assertNotIn('api.searchOperators', text, name)
        # Окно особого контроля доезжает до нового роста при выборе человека.
        controls = (self.DIR / 'ControlsPanel.jsx').read_text(encoding='utf-8')
        self.assertIn("sizeKey={operator ? 'picked' : 'pick'}", controls)

    def test_windows_start_at_the_top_edge(self):
        """Окно не лежит в столбике space-y: ребёнку space-y достаётся margin-top,
        и затемнение начиналось на 16–24 px ниже края экрана — светлая полоса над
        окном (замер на стенде 22.10.2026). Окно — сосед столбика во фрагменте."""
        for name, window in (('ControlsPanel.jsx', '<NewControlForm'), ('SettingsPanel.jsx', '<DictionaryEditor')):
            text = (self.DIR / name).read_text(encoding='utf-8')
            body = text[text.rindex('\n    return (\n'):]  # возврат самой панели — она последняя в файле
            self.assertTrue(body.startswith('\n    return (\n        <>\n            <div className="space-y-'), name)
            self.assertGreater(body.index(window), body.index('\n            </div>\n'), name)
        # Форма нарушения и окно записи — дети корня раздела, у него нет space-y.
        view = (self.DIR / 'DisciplineView.jsx').read_text(encoding='utf-8')
        root = re.search(r'return \(\s*<div className="(mx-auto w-full max-w-\[1180px\][^"]*)"', view)
        self.assertNotIn('space-y-', root.group(1))


class LadderSettingsTests(unittest.TestCase):
    """Владелец, 21.10.2026: лестница по умолчанию — как в ТЗ, но без подписи
    «как в ТЗ»; лестницы называют и заводят свои (глава отдела и выше)."""
    DIR = ROOT / 'src' / 'components' / 'discipline'

    def test_no_tz_labels_in_section(self):
        for path in sorted(self.DIR.glob('*.js*')):
            text = path.read_text(encoding='utf-8')
            for label in ('Как в ТЗ', 'Как на образце'):
                self.assertNotIn(label, text, path.name)

    def test_settings_edit_named_ladders(self):
        panel = (self.DIR / 'SettingsPanel.jsx').read_text(encoding='utf-8')
        self.assertIn('<LadderLibrary draft={draft}', panel)
        for text in ('Новая лестница', 'Сделать действующей', 'Удалить лестницу', 'label="Название"'):
            self.assertIn(text, panel)
        self.assertNotIn('presets', panel)
        # Проверка названий до отправки — та же, что на сервере.
        self.assertIn('ladderProblems(draft.ladders, data?.catalog?.ladders?.name_max)', panel)

    def test_department_picker_is_a_dropdown_on_phone(self):
        # Длинные названия отделов в сегменты телефона не влезали — там выпадающий список.
        ui = (self.DIR / 'disciplineUi.jsx').read_text(encoding='utf-8')
        self.assertIn('export const DepartmentPicker = ({ value, onChange, options, mobile, className = \'\' }) => (mobile ? (', ui)
        self.assertIn('<CustomSelect variant="ios" value={value} options={options} onChange={onChange} ariaLabel="Отдел" />', ui)
        for name in ('DisciplineView.jsx', 'SettingsPanel.jsx'):
            text = (self.DIR / name).read_text(encoding='utf-8')
            self.assertIn('<DepartmentPicker', text, name)
            self.assertIn('mobile={mobile}', text, name)
            self.assertNotIn('ariaLabel="Отдел"', text, name)


if __name__ == '__main__':
    unittest.main()
