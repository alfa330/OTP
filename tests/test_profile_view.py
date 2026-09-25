"""Раздел «Профиль» после переделки 25.09.2026 (просьба владельца: гармонично,
с симметрией, аккуратно и удобно в стиле iOS/macOS).

Тесты держат решения, до которых нельзя догадаться по коду раздела:

- ТЕЛЕФОН: сетки строк и полосы показателей заданы инлайн-стилем: эвристики оболочки
  телефона (mobile-shell.css) ставят `.flex.items-start` с `.flex-1` в колонку
  и сводят `grid-cols-*` к двум колонкам — строка «подпись — значение»
  разваливалась, а три показателя вставали «две плюс одна»;
- заголовков h3 в разделе нет: оболочка растягивает их до 16 px;
- корень на телефоне непрозрачный: у body логотип компании по центру экрана
  (styles.css), и в зазорах между группами он просвечивал серым пятном;
- на телефоне кнопок «Мои часы» / «Мои оценки» нет — их повторяет нижний бар;
  в разделы ведут ячейки показателей, и только в выданные отделу;
- КОМПЬЮТЕР — прежние цвета раздела в новой раскладке (владелец 25.09.2026:
  «пусть те же цвета останутся, просто поменяй расположение»): градиентные
  плитки, серые карточки с цветными значками, «Быстрые действия» — и всё на
  одной трёхколоночной сетке.
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


class ProfileScopeTests(unittest.TestCase):
    """Переделка — только СЗоВ, ОП и Тез КЦ (владелец 25.09.2026); остальным
    отделам — прежний «Профиль» без изменений."""

    app = _read(ROOT / 'src' / 'App.jsx')
    scope = _read(PROFILE / 'profileScope.js')
    legacy = _read(PROFILE / 'LegacyProfileView.jsx')

    def test_redesign_is_limited_to_three_departments(self):
        self.assertIn("export const PROFILE_REDESIGN_DEPARTMENT_CODES = ['szov', 'op', 'tez'];", self.scope)
        block = self.app.partition("{view === 'profile' && (() => {")[2].partition('})()}')[0]
        self.assertIn('if (!usesRedesignedProfile(user)) {', block)
        self.assertIn('return <LegacyProfileView {...profileViewProps} onOpenView={setView} />;', block)
        self.assertIn('<ProfileView\n', block)
        # «Мои данные» — только в новом виде (их отделы — те же три).
        legacy_branch = block.partition('if (!usesRedesignedProfile(user)) {')[2].partition('return (')[0]
        self.assertNotIn('MyDataCard', legacy_branch)

    def test_legacy_view_keeps_the_old_look(self):
        # Прежние приметы: шапка слева, три/четыре плитки, четыре карточки,
        # «Стаж работы (приблизительно)», «Быстрые действия» и прежняя ошибка.
        for marker in (
            "`grid grid-cols-2 ${isTezOp ? 'sm:grid-cols-4' : 'sm:grid-cols-3'} gap-3 sm:gap-4`",
            'flex flex-col sm:flex-row items-center gap-4 pb-4 sm:pb-6 border-b border-gray-200',
            'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4',
            'Стаж работы <span className="text-gray-400 normal-case">(приблизительно)</span>',
            'Быстрые действия',
            'Нету информации о профиле',
        ):
            self.assertIn(marker, self.legacy, marker)
        # Числа — из того же расчёта, второй копии часов здесь нет.
        self.assertNotIn('hoursData', self.legacy)
        self.assertNotIn('computeUniqueTrainingDurationHours', self.legacy)


class ProfileLayoutTests(unittest.TestCase):
    view = _read(PROFILE / 'ProfileView.jsx')
    phone = view.partition('function PhoneTop(')[2].partition('// ── Компьютер')[0]
    root = view.partition('export default function ProfileView(')[2]
    desktop = view.partition('// ── Компьютер')[2]
    desktop_ui = _read(PROFILE / 'profileDesktop.jsx')
    ui = _read(PROFILE / 'profileUi.jsx')
    my_data = _read(PROFILE / 'MyDataCard.jsx')
    css = _read(PROFILE / 'profile-mobile.css')
    app = _read(ROOT / 'src' / 'App.jsx')

    def test_grids_survive_phone_heuristics(self):
        # Только телефонная часть: на компьютере эвристик оболочки нет, и там
        # обычные утилиты сетки законны.
        head = self.view.partition('function PhoneTop(')[0]
        phone_my_data = self.my_data.partition('if (!isMobileShell) {')[0] + self.my_data.partition('// Вся группа — форма')[2]
        for name, source in (('PhoneTop', head + self.phone), ('profileUi', self.ui), ('MyDataCard', phone_my_data)):
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
        error = self.phone.partition("if (failed) {")[2][:600]
        self.assertIn('<section className={`${iosCard} px-5 py-10 text-center`}>', error)

    def test_edit_survives_refetch_and_layout_switch(self):
        # Корень один на обе раскладки, баннер ставки и «Мои данные» — на
        # постоянных местах: React не пересоздаёт их ни при переходе окна через
        # 768 px, ни при перезапросе — несохранённая правка не пропадает.
        order = [self.root.index(marker) for marker in (
            '{rateBanner}',
            '<PhoneTop {...props}',
            '{failed ? null : myData}',
        )]
        self.assertEqual(order, sorted(order))
        self.assertEqual(1, self.view.count('{failed ? null : myData}'))
        self.assertNotIn('{myData}', self.phone)
        self.assertNotIn('{myData}', self.desktop.partition('export default function ProfileView(')[0])
        # Скелетон — только пока профиля ещё нет.
        self.assertIn('const firstLoad = Boolean(loading && !profile);', self.root)

    def test_stat_cells_line_up(self):
        # Ячейка с третьей строкой («Осталось 59») не опускает числа соседей.
        self.assertIn("justifyContent: 'flex-start'", self.view)
        self.assertIn('style={cellStyle}', self.view)

    def test_no_h3_headings_on_phone(self):
        phone_my_data = self.my_data.partition('if (!isMobileShell) {')[0] + self.my_data.partition('// Вся группа — форма')[2]
        for name, source in (('PhoneTop', self.phone), ('profileUi', self.ui), ('MyDataCard', phone_my_data)):
            self.assertNotIn('<h3', source, name)
            self.assertNotIn('<h2', source, name)

    def test_phone_root_is_opaque_and_shares_the_phone_language(self):
        self.assertIn("? 'sa-m-root pf-m-root min-h-screen bg-slate-100'", self.root)
        self.assertIn("import '../resources/shift-auction-mobile.css';", self.ui)
        self.assertIn("isMobileShell ? 'sa-m-row ' : ''", self.ui)
        self.assertIn('sa-m-group__label', self.ui)
        rules = [line for line in self.css.splitlines() if line.rstrip().endswith('{')]
        self.assertTrue(rules)
        for rule in rules:
            self.assertTrue(rule.startswith('body.mobile-shell '), rule)

    def test_quick_actions_only_on_desktop_and_only_to_granted_views(self):
        self.assertNotIn('quickActions', self.phone)
        self.assertIn('quickActions.map((action) =>', self.desktop)
        self.assertIn(".filter((action) => departmentAllowsView(user, action.key))", self.app)
        builder = self.app.partition('const buildProfileStats = () => {')[2].partition('const fetchProfileData = async () => {')[0]
        self.assertIn("departmentAllowsView(user, viewKey) ? () => setView(viewKey) : null", builder)
        self.assertIn("openProfileView('hours')", builder)
        self.assertIn("openProfileView('evaluation')", builder)

    def test_single_profile_rendering(self):
        self.assertEqual(1, self.app.count('<ProfileView'))
        self.assertNotIn('ProfilePageSkeleton', self.app)

    def test_desktop_is_calm_macos_style(self):
        # Владелец отверг градиентные плитки («мультяшно»): белые карточки,
        # цвет — только в квадратных значках «Настроек», числа тёмные.
        for cartoon in ('bg-gradient-to-br from-green-50', 'from-purple-50 to-purple-100', 'from-orange-50 to-orange-100'):
            self.assertNotIn(cartoon, self.desktop)
        self.assertIn("text-white ${ICON_SIZE[size] || ICON_SIZE.row} ${ICON_BG[tone] || ICON_BG.blue}", self.desktop_ui)
        self.assertIn("<div key={stat.key} className={`${iosCard} p-6`}>", self.desktop)
        # Крупно и во всю ширину: узкая колонка с 14-м кеглем на большом
        # мониторе была «слишком всё мелкое, столько свободного пространства».
        self.assertIn('max-w-[1800px]', self.root)
        self.assertIn('text-[40px] font-semibold', self.desktop)
        self.assertIn('flex min-h-[60px] flex-1 gap-3 px-4 py-3 2xl:gap-4 2xl:px-5', self.desktop_ui)
        # Одна трёхколоночная сетка: «Работа» — колонка, «Мои данные» — две
        # соседние, строки обеих групп на общих горизонталях.
        self.assertIn("xl:grid-cols-3 [&>button]:mb-0 xl:[&>button]:col-span-3", self.root)
        self.assertIn("className={wide ? 'xl:col-span-3' : 'xl:col-span-1'}", self.desktop)
        self.assertIn('className="xl:col-span-2" onSubmit={save} noValidate', self.my_data)
        # Колонки «Моих данных» — одна сетка по строкам: у пары ячеек одна
        # высота, перенос длинного названия вуза не разводит горизонтали.
        self.assertIn('{[0, 1, 2].flatMap((i) => [ROWS[i], ROWS[i + 3]]).map(renderRow)}', self.my_data)
        # Шапка группы строгой высоты — «Сохранить» не распирает строку.
        self.assertIn('mb-2 flex h-9 items-center justify-between', self.desktop_ui)

    def test_edit_fields_match_the_course_picker(self):
        # Поля правки и список курсов — одного вида и кегля.
        self.assertIn('rounded-xl bg-white px-3 py-2', self.my_data)
        self.assertIn("textClassName={isMobileShell ? `${textSize(isMobileShell)} text-slate-900` : 'text-[16px] text-slate-900'}", self.my_data)
        self.assertIn('variant="ios"', self.my_data)
        # На компьютере поля того же вида: белые, тонкая обводка, мягкая тень.
        self.assertIn("'w-full min-w-0 rounded-lg bg-white px-3 py-2 text-[16px] text-slate-900 placeholder-slate-400 '", self.my_data)
        picker = _read(ROOT / 'src' / 'components' / 'ui' / 'CustomSelect.jsx')
        # У остальных мест сайта кнопка списка прежняя.
        self.assertIn("${textClassName || 'text-[12.5px] font-medium text-slate-700'}", picker)
        self.assertRegex(picker, re.compile(r"textClassName = ''"))


if __name__ == '__main__':
    unittest.main()
