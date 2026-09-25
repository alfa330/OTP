"""Раздел «Профиль» после переделки 25.09.2026 (просьба владельца: гармонично,
с симметрией, аккуратно и удобно в стиле iOS/macOS).

Тесты держат решения, до которых нельзя догадаться по коду раздела:

- сетки строк и полосы показателей заданы инлайн-стилем: эвристики оболочки
  телефона (mobile-shell.css) ставят `.flex.items-start` с `.flex-1` в колонку
  и сводят `grid-cols-*` к двум колонкам — строка «подпись — значение»
  разваливалась, а три показателя вставали «две плюс одна»;
- заголовков h3 в разделе нет: оболочка растягивает их до 16 px;
- корень на телефоне непрозрачный: у body логотип компании по центру экрана
  (styles.css), и в зазорах между группами он просвечивал серым пятном;
- кнопок «Мои часы» / «Мои оценки» больше нет — они повторяли меню и нижний бар;
  в разделы ведут ячейки показателей, и только в выданные отделу.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'src' / 'components' / 'profile'


def _read(path):
    return path.read_text(encoding='utf-8-sig')


def _code_only(source):
    """Исходник без комментариев: в них эти слова законно объясняют, почему
    их нет в коде. Адресов с «//» в файлах профиля нет."""
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return re.sub(r'(?m)//.*$', '', source)


class ProfileLayoutTests(unittest.TestCase):
    view = _read(PROFILE / 'ProfileView.jsx')
    ui = _read(PROFILE / 'profileUi.jsx')
    my_data = _read(PROFILE / 'MyDataCard.jsx')
    css = _read(PROFILE / 'profile-mobile.css')
    app = _read(ROOT / 'src' / 'App.jsx')

    def test_grids_survive_phone_heuristics(self):
        for name, source in (('ProfileView', self.view), ('profileUi', self.ui), ('MyDataCard', self.my_data)):
            code = _code_only(source)
            self.assertNotRegex(code, r'\bgrid-cols-', name)
            self.assertNotRegex(code, r'className="[^"]*\bflex items-start\b', name)
        self.assertIn("gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`", self.view)
        # Четыре показателя ОП TEZ на телефоне — квадратом 2×2: в ряд «123/160»
        # упиралось в разделители уже на 375 px.
        self.assertIn("const columns = isMobileShell && stats.length === 4 ? 2 : stats.length;", self.view)
        self.assertIn("gridTemplateColumns: `${labelColumn(isMobileShell)} minmax(0, 1fr)`", self.ui)

    def test_phone_edit_rows_stack_label_over_field(self):
        # В строку с колонкой подписей поле на 390 px получало 178 px.
        self.assertIn('stacked={editing && isMobileShell}', self.my_data)
        self.assertIn("gridTemplateColumns: 'minmax(0, 1fr)'", self.ui)

    def test_retry_reloads_the_whole_section(self):
        region = self.app.partition('onRetry={() => {')[2][:400]
        for call in ('fetchProfileData();', 'fetchHoursData();', 'fetchOperatorData();', 'fetchTrainings();'):
            self.assertIn(call, region)

    def test_error_card_is_not_flattened_on_phone(self):
        # Единственный div-ребёнок корня оболочка телефона делает плоским.
        error = self.view.partition("{!loading && !profile ? (")[2][:600]
        self.assertIn('<section className={`${iosCard} px-5 py-10 text-center`}>', error)

    def test_stat_cells_line_up(self):
        # Ячейка с третьей строкой («Осталось 59») не опускает числа соседей.
        self.assertIn("justifyContent: 'flex-start'", self.view)
        self.assertIn('style={cellStyle}', self.view)

    def test_no_h3_headings(self):
        for name, source in (('ProfileView', self.view), ('profileUi', self.ui), ('MyDataCard', self.my_data)):
            self.assertNotIn('<h3', source, name)
            self.assertNotIn('<h2', source, name)

    def test_phone_root_is_opaque_and_shares_the_phone_language(self):
        self.assertIn("'sa-m-root pf-m-root min-h-screen bg-slate-100'", self.view)
        self.assertIn("import '../resources/shift-auction-mobile.css';", self.ui)
        self.assertIn("isMobileShell ? 'sa-m-row ' : ''", self.ui)
        self.assertIn('sa-m-group__label', self.ui)
        rules = [line for line in self.css.splitlines() if line.rstrip().endswith('{')]
        self.assertTrue(rules)
        for rule in rules:
            self.assertTrue(rule.startswith('body.mobile-shell '), rule)

    def test_quick_action_duplicates_are_gone(self):
        self.assertNotIn('Быстрые действия', self.app)
        self.assertNotIn('Быстрые действия', self.view)
        builder = self.app.partition('const buildProfileStats = () => {')[2].partition('const fetchProfileData = async () => {')[0]
        self.assertIn("departmentAllowsView(user, viewKey) ? () => setView(viewKey) : null", builder)
        self.assertIn("openProfileView('hours')", builder)
        self.assertIn("openProfileView('evaluation')", builder)

    def test_single_profile_rendering(self):
        self.assertEqual(1, self.app.count('<ProfileView'))
        self.assertNotIn('ProfilePageSkeleton', self.app)

    def test_edit_fields_match_the_course_picker(self):
        # Поля правки и список курсов — одного вида и кегля.
        self.assertIn('rounded-xl bg-white px-3 py-2', self.my_data)
        self.assertIn('textClassName={`${textSize(isMobileShell)} text-slate-900`}', self.my_data)
        picker = _read(ROOT / 'src' / 'components' / 'ui' / 'CustomSelect.jsx')
        # У остальных мест сайта кнопка списка прежняя.
        self.assertIn("${textClassName || 'text-[12.5px] font-medium text-slate-700'}", picker)
        self.assertRegex(picker, re.compile(r"textClassName = ''"))


if __name__ == '__main__':
    unittest.main()
