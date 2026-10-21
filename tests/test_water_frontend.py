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

from water import access, notify, report, routes, rules, schema

ROOT = Path(__file__).resolve().parents[1]
PARCELS_VIEW = ROOT / 'src' / 'components' / 'parcels' / 'ParcelsView.jsx'
APP_JSX = ROOT / 'src' / 'App.jsx'
META = ROOT / 'src' / 'components' / 'water' / 'waterMeta.js'
PANEL = ROOT / 'src' / 'components' / 'water' / 'WaterPanel.jsx'
WATER = ROOT / 'src' / 'components' / 'water'


def _read(path):
    return path.read_text(encoding='utf-8-sig')


def _js_object(source, name):
    block = source.split('export const %s = {' % name)[1].split('};')[0]
    return dict(re.findall(r"(\w+):\s*'([^']*)'", block))


def _jsx_tags(source, name):
    """Открывающие теги <name …> целиком. В атрибутах стоят стрелки (=>), поэтому
    конец тега ищется по балансу фигурных скобок, а не по первой «>»."""
    tags, at = [], source.find('<' + name)
    while at >= 0:
        depth, index = 0, at
        while index < len(source):
            char = source[index]
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
            elif char == '>' and depth == 0:
                break
            index += 1
        tags.append(source[at:index + 1])
        at = source.find('<' + name, index)
    return tags


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
        """Супер-админ → ТЭЗ закрыт → руководитель → офисники по списку → СЗоВ и ОП,
        как в water.access.can_open_section: ТЭЗ закрыт раньше любых оснований."""
        predicate = self._predicate()
        steps = ["=== 'super_admin') return true;",
                 "if (own === 'tez' || headed.includes('tez')) return false;",
                 "if (isDepartmentHead(userLike) && headed.includes('front_office')) return true;",
                 "if (own === 'front_office') return WATER_ISSUER_USER_IDS.has(Number(userLike?.id));",
                 "return WATER_CHECK_DEPARTMENT_CODES.some((code) => own === code || headed.includes(code));"]
        positions = [predicate.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(access.CLOSED_DEPARTMENT_CODES, ('tez',))
        self.assertEqual(access.ISSUE_DEPARTMENT_CODE, 'front_office')
        self.assertIn("const WATER_CHECK_DEPARTMENT_CODES = ['szov', 'op'];", self.app)
        self.assertEqual(access.CHECK_DEPARTMENT_CODES, ('szov', 'op'))

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
        self.assertIn("    water: ['front_office', 'szov', 'op'],", self.app)
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

    def test_settings_limits_match_the_server(self):
        """Степпер «Настроек» не шагает за пределы, а набранное вне их краснеет у
        поля — пределы те же, что у сервера: разойдись они, степпер довёл бы до
        числа, которое сохранение отвергнет."""
        max_blocks = int(re.search(r'export const MAX_BLOCKS = (\d+);', self.meta).group(1))
        self.assertEqual(max_blocks, routes._MAX_BLOCKS)
        block = self.meta.split('export const SETTINGS_LIMITS = {')[1].split('};')[0]
        front = {name: (int(low), max_blocks if high == 'MAX_BLOCKS' else int(high))
                 for name, low, high in re.findall(r'(\w+): \[(\d+), (\w+)\]', block)}
        self.assertEqual(front, {name: (low, high) for name, (low, high, _message)
                                 in routes._SETTINGS_LIMITS.items()})
        # Каждое число программы на экране — строка со степпером, и наоборот.
        settings = _read(WATER / 'WaterSettings.jsx')
        shown = re.findall(r"\{ name: '(\w+)', label:", settings)
        self.assertEqual(sorted(shown), sorted(routes._SETTINGS_LIMITS))


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
        self.assertIn('const { rejected, inputProps, setValue } = useCountInput(onChange);', settings)
        self.assertIn('<input id={id} {...inputProps} value={value}', settings)
        count = _read(water / 'CountInput.jsx')
        # Шаг степпера — годное число: «Только цифры» гаснет вместе с ним.
        self.assertIn('const setValue = (value) => {\n        setRejected(false);\n        onChange(String(value));', count)
        handler = count.split('const handleChange = (event) => {')[1].split('\n    };')[0]
        self.assertLess(handler.index('if (!isCountDraft(next)) {'), handler.index('onChange(next);'))
        self.assertIn('setRejected(true);\n            return;', handler)
        self.assertNotIn('replace(', handler)

    def test_stock_shows_the_office_address(self):
        """«Отображать адрес самого офиса, не только название» (01.10.2026):
        одно название «Бизнес» есть и в Алматы, и в Астане."""
        stock = _read(ROOT / 'src' / 'components' / 'water' / 'WaterStock.jsx')
        self.assertEqual(stock.count('{officePlace(row)}'), 2)  # таблица и карточки на телефоне
        self.assertIn('subtitle={<ModalSubtitle>{officePlace(office)}</ModalSubtitle>}', stock)

    def test_recount_can_be_counted_in_intake(self):
        """«В случае пересчёта регулировать „Поступило“» (05.10.2026): отметка —
        в форме пересчёта и у уже сделанного пересчёта, и только руководителю."""
        stock = _read(ROOT / 'src' / 'components' / 'water' / 'WaterStock.jsx')
        self.assertIn("const INTAKE_FLAG_LABEL = 'Учесть в «Поступило»';", stock)
        # В форме — только на вкладке «Пересчёт», и уходит вместе с пересчётом.
        self.assertIn("{mode === 'recount' && (\n", stock)
        self.assertIn('<IosToggle checked={adjustsIntake} onChange={setAdjustsIntake} disabled={saving} />', stock)
        self.assertIn('{ stock: count, comment, adjusts_intake: adjustsIntake }', stock)
        # Отметка живёт один пересчёт: смена вкладки или офиса её снимает.
        self.assertIn('useEffect(() => { setAdjustsIntake(false); }, [mode, open, office?.id]);', stock)
        # У сделанного пересчёта — меню; поступлению отмечать нечего.
        self.assertIn("const recountMenu = canManage && movements.some((item) => item.kind === 'recount');", stock)
        self.assertIn("{recountMenu && (item.kind === 'recount' ? (", stock)
        self.assertIn('axios.patch(`${apiBaseUrl}/api/water/movements/${item.id}`', stock)
        self.assertIn('{ adjusts_intake: next }', stock)
        # «Поступило» в таблице перечитывается, остаток — нет: он не менялся.
        self.assertIn('onIntakeChanged={load}', stock)
        # Пояснение — под «i», а не строкой под полем.
        self.assertIn('<InfoHint side="left" text={INTAKE_FLAG_HINT} />', stock)

    def test_movement_rows_do_not_wrap_on_a_phone(self):
        """Оболочка телефона переносит ряды с gap: у строки с отметкой число и
        меню падали под текст, к левому краю (снимок 390×844). Правило оболочки
        перебивается только инлайном — класс `flex-nowrap` она сама переводит в
        перенос."""
        stock = _read(ROOT / 'src' / 'components' / 'water' / 'WaterStock.jsx')
        rows = stock.split('{movements.map((item) => (')[1].split('</ul>')[0]
        self.assertIn("<li key={item.id} style={{ flexWrap: 'nowrap' }}", rows)
        self.assertNotIn('flex-nowrap', rows)
        shell = _read(ROOT / 'src' / 'components' / 'common' / 'mobile-shell.css')
        self.assertIn('body.mobile-shell .main-content .flex.items-start.justify-between {', shell)

    def test_no_native_selects_or_date_inputs(self):
        """Эталон портала — свои пикеры: системный select/date — чужая деталь."""
        for path in (ROOT / 'src' / 'components' / 'water').glob('*.jsx'):
            source = _read(path)
            self.assertNotIn('<select', source, path.name)
            self.assertNotIn('type="date"', source, path.name)


class LargeScreenTests(unittest.TestCase):
    """«Раздел мелкий — и шрифты, и сами блоки; на большом экране смотрится не
    очень» (владелец, 12.10.2026). Размеры раздела растут двумя ступенями — xl
    (окно от 1280 px) и 2xl (от 1536 px): шкала в waterUi.jsx, раскладки в
    экранах. До 1280 px вид прежний пиксель в пиксель (сверено кадрами)."""

    def setUp(self):
        self.ui = _read(WATER / 'waterUi.jsx')
        self.screens = {path.name: _read(path) for path in WATER.glob('*.jsx')
                        if path.name != 'waterUi.jsx'}

    def test_scale_never_reaches_the_phone(self):
        """Добавки размера — только с префиксом xl или 2xl. Не md: оболочка
        телефона включается и у телефона боком (844–956 px ширины), и ровно на
        768 px, где md уже действует. Не lg: в окне 1024–1279 px таблицам тесно
        и с прежним кеглем. Утилита без префикса поменяла бы телефон, а поверх
        готового ios-класса того же свойства ещё и молча проиграла бы: побеждает
        порядок правил в сборке, а не порядок в строке."""
        addons = re.findall(r"'([^']*)'", self.ui.split('export const T = {')[1].split('};')[0])
        self.assertEqual(len(addons), 6)
        for name in ('BUTTON', 'wChip', 'wPill', 'wIcon', 'wTabs', 'wSegments'):
            block = re.search(r"const %s = ((?:\s*\+?\s*'[^']*')+);" % name, self.ui)
            self.assertIsNotNone(block, name)
            addons += re.findall(r"'([^']*)'", block.group(1))
        for name in ('wInput', 'wBtnGhost'):
            block = re.search(r"export const %s = `\$\{ios\w+\}([^`]*)`;" % name, self.ui)
            self.assertIsNotNone(block, name)
            addons.append(block.group(1))
        utilities = ' '.join(addons).split()
        self.assertGreater(len(utilities), 40)
        for utility in utilities:
            self.assertRegex(utility, r'^(xl|2xl):', utility)
            # Оболочка телефона правит всё, в чьём классе есть «w-[», — даже с префиксом.
            self.assertNotRegex(utility, r':(min-)?w-\[', utility)
        # Ступеней ниже xl в разделе нет: из sm, md и lg остались только прежние
        # переключатели раскладки (таблица на компьютере — карточки на телефоне,
        # ряд поиска, сетка фильтров, поля рамки раздела).
        low = sorted(re.findall(r'(?<![\w-])(?:sm|md|lg):[^\s`\'"]+', ' '.join(self.screens.values()) + self.ui))
        self.assertEqual(low, ['lg:grid-cols-3', 'md:block', 'md:block', 'md:hidden', 'md:hidden',
                               'sm:flex-1', 'sm:flex-none', 'sm:flex-row', 'sm:grid-cols-2', 'sm:items-center',
                               'sm:left-auto', 'sm:px-5', 'sm:py-6', 'sm:right-0', 'sm:w-auto'])

    def test_base_classes_are_carried_over_untouched(self):
        """Классы без префикса в шкале — те, что стояли в разметке до правки:
        они и есть вид раздела до 1280 px (таблицы видны с 768)."""
        # Таблицы растут только с 2xl: в окне 1280 px журнал с кеглем 14 px и
        # полями шире уезжал вбок на 45 px (замер 12.10.2026), прежним — входит.
        self.assertIn("export const wTable = 'w-full text-left text-[13px] 2xl:text-[16px]';", self.ui)
        self.assertIn("export const wThead = 'bg-slate-50/80 text-[11.5px] uppercase tracking-wide text-slate-500 2xl:text-[12.5px]';", self.ui)
        self.assertIn("export const wTh = 'px-3.5 py-2.5 font-semibold 2xl:whitespace-nowrap 2xl:px-5 2xl:py-3.5';", self.ui)
        self.assertIn("export const wTd = 'px-3.5 py-2.5 2xl:px-5 2xl:py-4';", self.ui)
        self.assertIn("export const wTdSub = '2xl:text-[14px]';", self.ui)
        self.assertIn("export const wPill = '2xl:px-2.5 2xl:py-1 2xl:text-[13px]';", self.ui)
        journal = self.screens['WaterJournal.jsx']
        self.assertIn("<table className={`${wTable} 2xl:min-w-[1310px]`}>", journal)
        self.assertIn("const OFFICE_CELL = '2xl:min-w-[215px]';", journal)
        self.assertIn("const WIDE_CELL = 'max-w-[220px] 2xl:max-w-[240px] min-[1800px]:max-w-[320px]';", journal)
        # Длинное ФИО сотрудника не отнимает ширину у офиса: дата не переносится,
        # сотрудник под ней обрезается, полное имя — в подсказке.
        self.assertIn("const DATE_CELL = 'xl:max-w-[190px] 2xl:max-w-[205px] min-[1800px]:max-w-[280px]';", journal)
        self.assertIn("<div className={`whitespace-nowrap tabular-nums ${main}`}>{fmtDateTime(item.created_at)}</div>", journal)
        self.assertIn("<div className={`truncate text-[12px] ${wTdSub} ${meta}`} title={item.issued_by_name || undefined}>", journal)
        # Группа — двойник IosSection: те же отступы и кегль пояснения.
        ios = _read(ROOT / 'src' / 'components' / 'ui' / 'ios.jsx')
        self.assertIn('<section className="space-y-1.5">', ios)
        self.assertIn("<div className={`${iosCard} p-4 space-y-3`}>", ios)
        self.assertIn('<div className="px-1 text-[11px] text-slate-500">{hint}</div>', ios)
        self.assertIn("<section className={`space-y-1.5 2xl:space-y-2 ${className}`}>", self.ui)
        self.assertIn("<div className={`${iosCard} space-y-3 p-4 xl:p-5 2xl:space-y-4 2xl:p-6`}>", self.ui)
        self.assertIn("<div className={`px-1 text-[11px] text-slate-500 ${T.note}`}>{hint}</div>", self.ui)
        # Красная кнопка отмены — прежняя строка классов плюс добавки кнопки.
        self.assertIn("'inline-flex items-center justify-center gap-2 rounded-xl bg-red-600 px-4 py-2.5 '", self.ui)
        self.assertIn("'text-[13.5px] font-semibold text-white transition hover:bg-red-700 active:scale-[0.98] disabled:opacity-50 '", self.ui)

    def test_controls_of_one_row_share_one_height(self):
        """Поле, список, чип периода, кнопка и полоса вкладок стоят в одних рядах:
        41 px на компьютере с xl (кегль 14 px, поля по 10) и 48 px на большом экране
        (строка 24 px, поля по 12). Замер в браузере 12.10.2026 — 41 и 48 у всех."""
        self.assertIn("export const wInput = `${iosInput} 2xl:px-4 2xl:py-3 2xl:text-[16px]`;", self.ui)
        self.assertIn("const BUTTON = 'xl:text-[14px] 2xl:px-5 2xl:py-3 2xl:text-[15px] 2xl:leading-6';", self.ui)
        self.assertIn("export const wChip = 'xl:px-3.5 xl:py-2.5 xl:text-[14px] 2xl:gap-2.5 2xl:px-4 2xl:py-3 2xl:text-[16px] '", self.ui)
        self.assertIn("export const wTabs = 'xl:[&>button]:px-4 xl:[&>button]:py-[7px] xl:[&>button]:text-[14px] '", self.ui)
        self.assertIn("'2xl:[&>button]:text-[15px] 2xl:[&>button]:leading-6'", self.ui)
        self.assertIn("2xl:[&>button]:py-2 '", self.ui)
        select = _read(ROOT / 'src' / 'components' / 'ui' / 'CustomSelect.jsx')
        self.assertIn("  trigger: ' xl:px-3.5 xl:py-2.5 2xl:px-4 2xl:py-3',", select)
        self.assertIn("  triggerText: ' xl:text-[14px] 2xl:text-[16px]',", select)

    def test_wide_layouts_keep_the_stacked_order_below_xl(self):
        """С xl карточка водителя и окно офиса — в две колонки. Колонки — обёртки,
        до xl растворённые (contents), а порядок в столбике держат order-классы:
        ответ остаётся над прошлыми выдачами, «В учёте» — под движениями."""
        issue = self.screens['WaterIssue.jsx']
        self.assertEqual(issue.count('<div className="contents xl:flex xl:flex-col '), 2)
        card = issue[issue.index('{driver && verdict && ('):]
        orders = re.findall(r'className=\{?[`"]order-(\d)', card)
        self.assertEqual(orders, ['1', '3', '2', '4'])  # в разметке: водитель, выдачи | ответ, действие
        # Ряд «Офис | Водитель» — доли из двенадцати: сетку в квадратных скобках
        # оболочка телефона складывает в одну колонку по подстроке «grid-cols-[».
        self.assertIn("${fieldsRow ? 'xl:grid-cols-12' : ''}", issue)
        # min-w-0 у обоих блоков: без него колонка сетки растёт под длинное
        # название офиса, и на телефоне список выезжал за край поля на 7 px
        # (поймано попиксельным сравнением с прежней сборкой).
        self.assertIn("<div className={`min-w-0 space-y-1.5 2xl:space-y-2 ${fieldsRow ? 'xl:col-span-5' : ''}`}>", issue)
        self.assertIn("<div className={`min-w-0 space-y-1.5 2xl:space-y-2 ${fieldsRow ? 'xl:col-span-7' : ''}`}>", issue)
        stock = self.screens['WaterStock.jsx']
        self.assertIn("const twoPane = canManage && office.is_active;", stock)
        self.assertIn("twoPane ? 'contents xl:sticky xl:top-4 xl:flex xl:flex-col xl:gap-4 2xl:gap-6' : 'contents'", stock)
        sheet = stock[stock.index('<IosModal open={open} onClose={onClose} title={<ModalTitle>{office.name}'):]
        sheet = sheet[:sheet.index('</IosModal>')]
        self.assertEqual(re.findall(r'order-(\d)', sheet), ['1', '2', '4', '3'])  # остаток, форма, «В учёте» | движения
        # Сеток без префикса в экранах (на телефоне это колонки) две: два порога в
        # карточке офиса и плитки тарифов в «Настройках» — по две в ряд на телефоне
        # (21.10.2026, вид «Настроек» как список iOS).
        for name, source in self.screens.items():
            source = re.sub(r'/\*[\s\S]*?\*/', '', source)  # в комментариях подстрока названа словами
            self.assertNotIn('grid-cols-[', source, name)
            bare = re.findall(r'(?<![\w:-])grid-cols-\d+', source)
            self.assertEqual(bare, ['grid-cols-2'] if name in ('WaterStock.jsx', 'WaterSettings.jsx') else [], name)

    def test_screens_take_shared_classes_through_the_scale(self):
        """Поле, кнопка и подпись приходят из waterUi.jsx, а не из ui/ios напрямую:
        иначе новое поле раздела снова вышло бы мелким рядом с крупными."""
        for name, source in self.screens.items():
            imported = re.search(r"import \{([^}]*)\} from '\.\./ui/ios';", source)
            if not imported:
                continue
            for shared in ('iosInput', 'iosBtnPrimary', 'iosBtnSecondary', 'iosBtnGhost',
                           'iosGroupLabel', 'IosSection'):
                self.assertNotIn(shared, imported.group(1), '%s берёт %s мимо waterUi' % (name, shared))

    def test_every_list_and_calendar_is_roomy(self):
        """Крупная кнопка не раскрывает список кеглем 12,5 px, а чип периода —
        календарь прежнего размера: у общих пикеров для этого параметр roomy."""
        seen = {}
        for name, source in self.screens.items():
            for tag_name in ('CustomSelect', 'IosDateRangePicker', 'IosDateRangeCalendar'):
                for tag in _jsx_tags(source, tag_name):
                    seen[tag_name] = seen.get(tag_name, 0) + 1
                    self.assertRegex(tag, r'\broomy\b', '%s: %s' % (name, tag[:70]))
        self.assertEqual(seen, {'CustomSelect': 8, 'IosDateRangePicker': 2, 'IosDateRangeCalendar': 1})

    def test_shared_pickers_are_unchanged_without_the_flag(self):
        select = _read(ROOT / 'src' / 'components' / 'ui' / 'CustomSelect.jsx')
        self.assertIn('  roomy = false,\n', select)
        self.assertIn('const roomyOn = isIos && roomy;', select)
        # Список рисуется только раскрытым, рендер-тест его строк не видит. Поэтому
        # все ступени размера живут в одной таблице и включаются одной функцией:
        # вне таблицы в коде компонента нет ни одной утилиты с брейкпоинтом.
        self.assertIn("const roomyAdd = (place) => (roomyOn ? ROOMY[place] : '');", select)
        table = select.split('const ROOMY = {')[1].split('};')[0]
        utilities = ' '.join(re.findall(r"'([^']*)'", table)).split()
        self.assertGreater(len(utilities), 12)
        for utility in utilities:
            self.assertRegex(utility, r'^(xl|2xl):', utility)
        code = re.sub(r'/\*[\s\S]*?\*/|//[^\n]*', '', select.replace(table, ''))
        self.assertNotRegex(code, r'\b(sm|md|lg|xl|2xl):')
        self.assertEqual(len(re.findall(r'ROOMY\b', code)), 2)  # объявление и roomyAdd
        picker = _read(ROOT / 'src' / 'components' / 'ui' / 'DateRangePicker.jsx')
        self.assertEqual(picker.count('roomy = false'), 2)  # календарь и обёртка-чип
        self.assertIn('presets={presets} roomy={roomy}', picker)
        addons = ' '.join(re.findall(r"roomy \? '([^']*)'", picker)).split()
        self.assertGreater(len(addons), 12)
        for utility in addons:
            self.assertTrue(utility.startswith('2xl:'), utility)
        # Ширину ячейке даёт колонка сетки (w-full): «w-[» в классе ловит оболочка
        # телефона и правит календарь даже по добавке с префиксом. У самой
        # карточки «w-[268px]» стояло и раньше.
        self.assertEqual([u for u in addons if ':w-[' in u], ['2xl:w-[326px]'])

    def test_windows_start_at_the_top_edge(self):
        """Окно — ребёнок столбика экрана. Отступ space-y доставался и ему:
        затемнение начиналось на 12 px ниже верхнего края (замер 12.10.2026 —
        окно стояло с 12-го пикселя, а не с нулевого). У столбика с gap такого
        отступа нет."""
        for name, windows in (('WaterStock.jsx', ('<OfficeSheet', '<AddOffice')),
                              ('WaterJournal.jsx', ('<IssueSheet',))):
            body = self.screens[name]
            body = body[body.rindex('\n    return (\n'):]  # возврат самого экрана, не строки списка
            root = body.split('<div className="', 1)[1].split('"', 1)[0]
            self.assertTrue(root.startswith('flex flex-col gap-3 '), '%s: %s' % (name, root))
            self.assertNotIn('space-y-', root)
            for window in windows:
                self.assertIn(window, body, name)

    def test_section_column_grows_to_the_profile_width(self):
        view = _read(WATER / 'WaterView.jsx')
        # До 1280 px потолок прежний, с xl — 1280, на большом экране — как у «Профиля».
        self.assertIn(' max-w-[1180px] px-3 py-4 sm:px-5 sm:py-6 xl:max-w-[1280px] 2xl:max-w-[1800px] ', view)
        self.assertEqual(len(re.findall(r'max-w-\[', view)), 3)
        profile = _read(ROOT / 'src' / 'components' / 'profile' / 'ProfileView.jsx')
        self.assertIn('max-w-[1800px]', profile)

    def test_big_screen_step_is_the_stock_tailwind_one(self):
        """Порог большого экрана в расчётах раздела — 1536 px, то есть 2xl из
        набора Tailwind по умолчанию. Свой `screens` в конфиге сдвинул бы его молча."""
        self.assertNotIn('screens', _read(ROOT / 'tailwind.config.cjs'))


class SettingsLayoutTests(unittest.TestCase):
    """«Настройки» — список настроек iOS/macOS (владелец 21.10.2026: «выглядит всё
    криво и не симметрично, сделай аккуратно и удобно»). Было: группы сеткой из
    трёх колонок разной высоты, пояснения строками под полями, на телефоне поле
    падало под подпись, выбранный тариф — чёрной заливкой."""

    def setUp(self):
        self.settings = _read(WATER / 'WaterSettings.jsx')

    def test_groups_are_full_width_cards_with_equal_columns(self):
        """Карточка группы — во всю ширину раздела, строки в ней с xl — в две
        равные колонки: края и середины карточек совпадают. Колонок групп разной
        высоты больше нет."""
        self.assertEqual(self.settings.count('grid xl:grid-cols-2`}>'), 2)  # «Право на воду», пороги
        self.assertNotIn('row-span', self.settings)
        self.assertNotIn('xl:grid-cols-3', self.settings)
        # Список получателей встаёт под правую колонку — вровень с порогами над ним.
        self.assertIn('<div className="min-w-0 xl:w-1/2 xl:pl-5 2xl:pl-6">', self.settings)
        self.assertIn("const CELL = 'min-w-0 px-4 xl:px-5 2xl:px-6';", self.settings)

    def test_rows_keep_the_field_beside_the_label_on_a_phone(self):
        """Оболочка телефона переносит ряды с justify-between, и поле падало под
        подпись к левому краю (снимок 390×844). Перебивается только инлайном."""
        self.assertIn("const NOWRAP = { flexWrap: 'nowrap' };", self.settings)
        self.assertEqual(self.settings.count('style={NOWRAP}'), 2)  # строка числа и плитка тарифа
        code = re.sub(r'/\*[\s\S]*?\*/', '', self.settings)  # в комментарии класс назван словами
        self.assertNotIn('flex-nowrap', code)

    def test_hints_live_under_the_i(self):
        """Пояснение — под «i» у подписи, а не строкой под полем; строкой под
        подписью бывает только то, что надо исправить."""
        self.assertIn('<InfoHint side="left" text={hint} className="ml-1.5 align-middle" />', self.settings)
        self.assertIn('{issue && <div className={ISSUE}>{issue}</div>}', self.settings)
        self.assertNotIn('<WaterGroup', self.settings)  # у неё пояснение — строкой под карточкой

    def test_selected_tariff_is_soft_blue_not_black(self):
        """Выбор — мягкая голубая заливка с галочкой (16.10.2026: чёрный выбор
        «выглядит плохо»)."""
        self.assertIn("? 'bg-blue-50 text-blue-700 ring-blue-200'", self.settings)
        self.assertNotIn('bg-slate-900', self.settings)


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

    def test_recount_mark_lives_in_fresh_and_live_bases(self):
        """Отметка «учесть в „Поступило“» — и в CREATE (пустая база), и миграцией
        (живая), по умолчанию выключена: прежние пересчёты «Поступило» не меняют."""
        table = self.ddl.split('CREATE TABLE IF NOT EXISTS water_movements')[1].split('CREATE TABLE')[0]
        self.assertRegex(table, r'adjusts_intake\s+BOOLEAN NOT NULL DEFAULT FALSE')
        migrations = '\n'.join(schema._MIGRATIONS)
        self.assertIn('ALTER TABLE water_movements ADD COLUMN IF NOT EXISTS adjusts_intake '
                      'BOOLEAN NOT NULL DEFAULT FALSE', migrations)
        for column in ('adjusts_intake_by_name', 'adjusts_intake_at'):
            self.assertIn(column, table)
            self.assertIn('ALTER TABLE water_movements ADD COLUMN IF NOT EXISTS %s ' % column, migrations)

    def test_movements_are_altered_after_issues(self):
        """Дашборд берёт таблицы в порядке «выдачи, движения». Разворот на старте
        запирает их в том же порядке — иначе он и запрос ещё живого старого
        экземпляра могли бы запереть друг друга крест-накрест."""
        tables = [statement.split('ALTER TABLE ')[1].split()[0]
                  for statement in schema._MIGRATIONS if statement.startswith('ALTER TABLE ')]
        self.assertEqual(tables, sorted(tables, key=('water_issues', 'water_movements').index))

    def test_tables_before_indexes(self):
        executed = []

        class Cursor:
            def execute(self, statement, params=None):
                executed.append(statement)

        schema.init_water_schema(Cursor())
        tables = [i for i, s in enumerate(executed) if 'CREATE TABLE' in s.upper()]
        others = [i for i, s in enumerate(executed) if 'CREATE TABLE' not in s.upper()]
        self.assertLess(max(tables), min(others))


class DriverCardNoteTests(unittest.TestCase):
    """Красная подпись состояния водителя в карточке «Выдачи» и «Проверки»."""

    def setUp(self):
        self.issue = _read(ROOT / 'src' / 'components' / 'water' / 'WaterIssue.jsx')

    def test_blocked_card_is_named_by_its_cause_not_as_a_park_block(self):
        """`is_blocked` в CRM — «карточка заблокирована сменой номера», а не запрет
        работать: 12.10.2026 «Заблокирован в парке» стояло у водителя со статусом
        `working` и 99 заказами за неделю. Само выражение подписи из WaterIssue.jsx,
        выполненное node: проверяем поведение, а не текст."""
        self.assertNotIn('Заблокирован в парке', self.issue)
        node = shutil.which('node')
        if not node:
            self.skipTest('node недоступен')
        start = self.issue.index('const WORK_STATUS = {')
        consts = self.issue[start:self.issue.index(';', self.issue.index('const CARD_BLOCKED = ')) + 1]
        expression = self.issue.split('const statusNote = ')[1].split(';')[0]
        cases = [
            ({'fired': False, 'is_blocked': True, 'work_status': 'working'},
             'Сменился номер — карточка в CRM заблокирована'),
            ({'fired': True, 'is_blocked': True, 'work_status': 'fired'}, 'Уволен из парка'),
            ({'fired': False, 'is_blocked': False, 'work_status': 'not_working'}, 'Не работает'),
            ({'fired': False, 'is_blocked': False, 'work_status': 'working'}, None),
        ]
        script = '\n'.join((
            consts,
            'const note = (driver) => %s;' % expression,
            'process.stdout.write(JSON.stringify(%s.map(note)));'
            % json.dumps([driver for driver, _ in cases]),
        ))
        out = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True, check=True)
        self.assertEqual(json.loads(out.stdout.decode('utf-8')), [expected for _, expected in cases])


if __name__ == '__main__':
    unittest.main()
