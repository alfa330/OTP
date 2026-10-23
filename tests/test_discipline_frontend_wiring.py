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


def _js_call(name, *args):
    """Результат функции disciplineRules.js на аргументах — через node."""
    script = "import(%s).then((m) => console.log(JSON.stringify(m[%s](...%s))))" % (
        json.dumps(RULES.as_uri()), json.dumps(name), json.dumps(list(args)))
    out = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True,
                         check=True, timeout=30)
    return json.loads(out.stdout)


class AppWiringTests(unittest.TestCase):
    def test_lazy_view_and_render_without_qr_lock(self):
        # Владелец, 23.10.2026: «для операторов сделать доступ без qr» — раздел
        # рисуется сразу, без общего QR-замка портала.
        self.assertIn("const DisciplineView = lazyWithRetry(() => import('./components/discipline/DisciplineView'));", APP)
        start = APP.index('{view === "discipline" && canAccessDisciplineSection && (\n')
        block = APP[start:APP.index('</Suspense>', start)]
        self.assertIn('focusRequest={disciplineFocusRequest}', block)
        self.assertNotIn('sensitiveSectionsLocked', block)
        self.assertNotIn('SensitiveSectionGate', block)

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
        # Статус QR раздел больше не спрашивает: лишний запрос при каждом входе.
        start = APP.index("if (view === 'complaints' || view === 'crm_tickets' || view === 'wiki'")
        condition = APP[start:APP.index(') {', start)]
        self.assertIn("view === 'baiga'", condition)
        self.assertNotIn("'discipline'", condition)

    def test_department_lists_match_server(self):
        match = re.search(r"const DISCIPLINE_DEPARTMENT_CODES = \[([^\]]*)\];", APP)
        self.assertTrue(match)
        codes = tuple(re.findall(r"'([a-z_]+)'", match.group(1)))
        self.assertEqual(codes, catalog.SECTION_DEPARTMENT_CODES)
        self.assertIn("discipline: ['szov', 'op', 'tez'],", APP)

    def test_rollout_flag_matches_server(self):
        # Пока раздел открыт супер-админам и поимённому списку — и в меню, и на сервере.
        match = re.search(r"const DISCIPLINE_SUPER_ADMIN_ONLY = (true|false);", APP)
        self.assertTrue(match)
        self.assertEqual(match.group(1) == 'true', access.SUPER_ADMIN_ONLY)
        named = re.search(r"const DISCIPLINE_ROLLOUT_USER_IDS = new Set\(\[([0-9, ]*)\]\);", APP)
        self.assertTrue(named)
        self.assertEqual({int(x) for x in named.group(1).split(',') if x.strip()}, set(access.ROLLOUT_USER_IDS))
        body = APP[APP.index('const canAccessDisciplineSectionForUser'):APP.index('const isDisciplineOperatorUser')]
        gate = 'if (DISCIPLINE_SUPER_ADMIN_ONLY && !DISCIPLINE_ROLLOUT_USER_IDS.has(Number(userLike?.id))) return false;'
        # Поимённый список снимает только флаг — роли проверяются дальше, как у всех.
        self.assertLess(body.index("if (role === 'super_admin') return true;"), body.index(gate))
        self.assertLess(body.index(gate), body.index('isDepartmentHead(userLike)'))

    def test_admin_without_heading_is_out(self):
        body = APP[APP.index('const canAccessDisciplineSectionForUser'):APP.index('const isDisciplineOperatorUser')]
        # Порядок важен: глава отдела раздела проходит раньше, чем админ отсекается.
        self.assertLess(body.index('isDepartmentHead(userLike)'), body.index("if (role === 'admin') return false;"))

    def test_no_qr_on_either_side(self):
        # Разойдись стороны — экран показал бы раздел, а сервер ответил бы отказом
        # без кнопки QR (или наоборот: замок там, где всё открыто).
        routes = (ROOT / 'discipline' / 'routes.py').read_text(encoding='utf-8')
        view = (ROOT / 'src' / 'components' / 'discipline' / 'DisciplineView.jsx').read_text(encoding='utf-8')
        self.assertFalse(hasattr(access, 'requires_sensitive_qr'))
        for text in (routes, view):
            self.assertNotIn('SENSITIVE_ACCESS_REQUIRED', text)
        self.assertNotIn('sensitive_access_granted', routes)

    def test_blueprint_call_matches_factory(self):
        # Регистрация блюпринта в bot_schedule2 обёрнута в try/except: лишний или
        # пропавший аргумент там не уронит запуск, а молча выключит раздел на проде.
        import inspect
        from discipline.routes import build_discipline_blueprint
        bot = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        start = bot.index('app.register_blueprint(build_discipline_blueprint(')
        call = bot[start:bot.index('))', start)]
        passed = set(re.findall(r'^\s+(\w+)=', call, re.M))
        params = inspect.signature(build_discipline_blueprint).parameters
        required = {name for name, p in params.items() if p.default is inspect.Parameter.empty}
        self.assertLessEqual(passed, set(params))
        self.assertLessEqual(required, passed)

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


class DocumentTemplatesTests(unittest.TestCase):
    """Владелец, 23.10.2026: шаблон объяснительной и акт по образцу организации.
    Бланк скачивают там, где объяснительную загружают; шапка бланка («кому») —
    настройка отдела, граница на клиенте та же, что на сервере."""
    DIR = ROOT / 'src' / 'components' / 'discipline'

    def test_blank_download_sits_next_to_explanation_upload(self):
        api = (self.DIR / 'disciplineApi.js').read_text(encoding='utf-8')
        self.assertIn('explanationBlank: (id) => blob(`/records/${id}/explanation.docx`),', api)
        for name in ('RecordSheet.jsx', 'MyRecords.jsx'):
            text = (self.DIR / name).read_text(encoding='utf-8')
            self.assertIn('saveBlob(await api.explanationBlank(item.id), `Объяснительная_${item.number}.docx`);',
                          text, name)
        # В карточке нарушения — ссылкой и только пока объяснительную ждут: главное
        # действие секции — загрузка, бланк — подсобное (разбор, 23.10.2026).
        sheet = (self.DIR / 'RecordSheet.jsx').read_text(encoding='utf-8')
        self.assertIn('{!done && (\n                                <button type="button" '
                      'className={`${dLinkAction} ${dLinkEdge}`} onClick={blank} disabled={busy}>', sheet)
        # Загрузка после ссылки «Бланк» — без сдвига влево, первая в ряду (бланк не нужен) — со сдвигом.
        self.assertIn('invalid={!!errors.upload}\n                                          edge={Boolean(done)} />',
                      sheet)
        self.assertIn('onFiles={upload} disabled={busy} invalid={!!errors.file} edge={false} />', sheet)
        # Оператор — в блоке «Нужна объяснительная», рядом с загрузкой фото; ряд не
        # схлопывается на время работы, занятость — у каждой записи своя.
        mine = (self.DIR / 'MyRecords.jsx').read_text(encoding='utf-8')
        self.assertLess(mine.index('onClick={() => blank(item)}'), mine.index('label="Загрузить фото или скан"'))
        self.assertIn("{busy[item.id] === 'blank' ? <Spinner size={14} /> : <Download size={14} />}", mine)
        self.assertIn("disabled={Boolean(busy[item.id])} busy={busy[item.id] === 'upload'}", mine)
        self.assertNotIn('<Spinner /> : (', mine)

    def test_link_actions_shift_only_at_the_row_edge(self):
        """Сдвиг влево — только у первой в ряду: после соседней кнопки ссылка
        наезжала на неё на 4–8 px (замер разбора на 1920)."""
        ui = (self.DIR / 'disciplineUi.jsx').read_text(encoding='utf-8')
        self.assertIn('export const dLinkAction = dBtnGhost;', ui)
        self.assertIn("export const dLinkEdge = '-ml-2 2xl:-ml-4';", ui)
        self.assertIn('busy = false, edge = true }) => {', ui)
        # Классы кнопки — одной строкой на обе её формы (кнопка и меню «Загрузить / Сделать скан»).
        self.assertIn("const className = `${dLinkAction} ${edge ? dLinkEdge : ''} ", ui)
        body = ui[ui.index('export const AttachButton'):ui.index('export const ACCEPT_DOCS')]
        self.assertEqual(body.count('className={className}'), 2)
        self.assertIn('trigger={{ icon, text: label, className }}', ui)
        panel = (self.DIR / 'SettingsPanel.jsx').read_text(encoding='utf-8')
        self.assertIn('invalid={!!templateError}\n                                                  edge={false} />', panel)

    def test_settings_edit_addressee_with_server_limits(self):
        panel = (self.DIR / 'SettingsPanel.jsx').read_text(encoding='utf-8')
        self.assertIn("value={draft.explanation_addressee || ''}", panel)
        self.assertIn('addresseeProblem(draft.explanation_addressee, data?.catalog?.addressee)', panel)
        # Поле сохраняется общей кнопкой — значит, стоит выше её панели.
        self.assertLess(panel.index('<Group title="Бланк объяснительной"'), panel.index('{dirty && ('))
        # Ошибка в «Кому» или сроках — окно само подводит к полю.
        for anchor in ('period_days', 'control_days', 'explanation_days', 'explanation_addressee'):
            self.assertIn('anchor="%s"' % anchor, panel)
        self.assertIn('.map((key) => document.querySelector(`[data-field="${key}"]`)).filter(Boolean)\n'
                      '                .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)[0]\n'
                      "                ?.scrollIntoView({ behavior: 'smooth', block: 'center' }));", panel)

    def test_addressee_is_normalized_like_on_the_server(self):
        limits = {'max_lines': catalog.ADDRESSEE_MAX_LINES, 'max_chars': catalog.ADDRESSEE_MAX_CHARS}
        # Разделители строк и управляющие символы приходят вставкой из Word, PDF,
        # 1С и Excel; длина — в символах (эмодзи — один символ, как в Python).
        for text in ('  Заказчику:  ИП «Пример» \n\n  Примеровой А. Б. \n', 'а\tб\u00a0в', '', '\n \n',
                     'одна строка', 'a\x0bb\r\nc\x08d\x85e\ufeff  f', 'a\u2028b\u2029c', 'a\rb', 'x\x1cy\x1dz\x1ew',
                     'A\x0bB\x0cC\x85D\x1fE', '\ufeffЗаказчику', 'и\x00п', '😀' * catalog.ADDRESSEE_MAX_CHARS):
            self.assertEqual('\n'.join(_js_call('addresseeLines', text)), catalog.normalize_addressee(text), text)
            self.assertIsNone(_js_call('addresseeProblem', text, limits), text)
        for text in ('\n'.join('строка %d' % i for i in range(catalog.ADDRESSEE_MAX_LINES + 1)),
                     '\r'.join('строка %d' % i for i in range(catalog.ADDRESSEE_MAX_LINES + 1)),
                     'я' * (catalog.ADDRESSEE_MAX_CHARS + 1), '😀' * (catalog.ADDRESSEE_MAX_CHARS + 1)):
            with self.assertRaises(catalog.SettingsError):
                catalog.normalize_addressee(text)
            self.assertTrue(_js_call('addresseeProblem', text, limits))


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


class ReportsPagingTests(unittest.TestCase):
    """Владелец, 23.10.2026: «в разделе отчёты реализовать пагинацию, чтобы оно
    не уходило вниз». Отчёт листается на клиенте, журнал действий — сервером,
    размер страницы один."""
    PANEL = (ROOT / 'src' / 'components' / 'discipline' / 'ReportsPanel.jsx').read_text(encoding='utf-8')

    def test_table_shows_one_page(self):
        self.assertIn(': reportPage(rows, page);', self.PANEL)
        self.assertIn('{paged.rows.map((row, index) => (', self.PANEL)
        self.assertNotIn('slice(0, 300)', self.PANEL)
        self.assertIn('<IosPager page={paged.page} pageCount={paged.pageCount} total={paged.total}', self.PANEL)

    def test_audit_pages_on_the_server_with_the_same_size(self):
        from discipline import routes
        self.assertIn('api.audit({ ...params, page: auditPage, page_size: REPORT_PAGE_SIZE })', self.PANEL)
        size = _js_object('REPORT_PAGE_SIZE')
        self.assertTrue(routes.AUDIT_PAGE_MIN <= size <= routes.AUDIT_PAGE_MAX, size)

    def test_turning_a_report_page_does_not_refetch_or_jump(self):
        # Номер страницы в зависимостях эффекта — только у журнала действий; новая
        # страница журнала не сменяется заготовкой строк (высота не прыгает).
        self.assertIn('}, [api, selected, params, auditPage, queryKey]);', self.PANEL)
        self.assertIn("const auditPage = selected === 'audit' ? page : 1;", self.PANEL)
        self.assertIn('if (shownQuery.current !== queryKey) {', self.PANEL)


class ScanFileTests(unittest.TestCase):
    """Скан документа (владелец, 23.10.2026: «сделать скан») уходит JPEG — одна
    страница — или PDF — несколько. Сервер проверяет файл по белому списку и
    сигнатуре (files.check), и скан обязан проходить её у каждого вида, куда
    кнопка «Приложить…» его предлагает: иначе человек снимет страницы, а
    загрузка ответит отказом."""

    SCAN = ROOT / 'src' / 'components' / 'discipline' / 'docScan.js'
    # JPEG 8×8, снятый с canvas, — тот же, что в tests/discipline_doc_scan.test.mjs.
    TINY_JPEG = ('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAx'
                 'NDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy'
                 'MjIyMjIyMjL/wAARCAAIAAgDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QA'
                 'FAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AKpgB//Z')

    def _scan(self):
        script = (
            "import(%s).then((m) => {"
            " const jpeg = Uint8Array.from(Buffer.from(%s, 'base64'));"
            " const pdf = m.buildPdf([{ jpeg, width: 1700, height: 2404 }, { jpeg, width: 1700, height: 2404 }]);"
            " const when = new Date(2026, 9, 23, 17, 40);"
            " console.log(JSON.stringify({ pdf: Buffer.from(pdf).toString('base64'),"
            "   pdfName: m.scanFileName(when, 'pdf'), jpgName: m.scanFileName(when, 'jpg') }));"
            "})" % (json.dumps(self.SCAN.as_uri()), json.dumps(self.TINY_JPEG)))
        out = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True,
                             check=True, timeout=30)
        return json.loads(out.stdout)

    def test_scan_passes_server_check_for_every_scanned_kind(self):
        import base64
        from discipline import files
        scan = self._scan()
        pdf = base64.b64decode(scan['pdf'])
        jpeg = base64.b64decode(self.TINY_JPEG)
        for kind in ('explanation', 'evidence', 'act', 'refusal_act'):
            name, ext, content_type = files.check(kind, filename=scan['pdfName'], data=pdf)
            self.assertEqual((name, ext, content_type), (scan['pdfName'], 'pdf', 'application/pdf'), kind)
            name, ext, content_type = files.check(kind, filename=scan['jpgName'], data=jpeg)
            self.assertEqual((name, ext, content_type), (scan['jpgName'], 'jpg', 'image/jpeg'), kind)
        # Имя дойдёт до списка файлов как есть: кириллица и пробелы сохраняются.
        self.assertEqual(scan['pdfName'], 'Скан 23.10.2026 17-40-00.pdf')

    def test_template_upload_offers_no_scan(self):
        # Шаблон акта — только DOCX: кнопка остаётся выбором файла (scanFormats ≠ any).
        settings = (ROOT / 'src' / 'components' / 'discipline' / 'SettingsPanel.jsx').read_text(encoding='utf-8')
        self.assertIn('accept=".docx"', settings)
        self.assertEqual(catalog.FILE_KINDS['act_template']['ext'], ('docx',))


class ScanWindowWiringTests(unittest.TestCase):
    """Проводка кнопки и окна скана, которую рендер первого кадра не видит
    (мутации разбора 23.10.2026 выживали)."""

    DIR = ROOT / 'src' / 'components' / 'discipline'
    UI = (DIR / 'disciplineUi.jsx').read_text(encoding='utf-8')
    SCANNER = (DIR / 'DocumentScanner.jsx').read_text(encoding='utf-8')

    def test_scan_reaches_on_files_as_a_list(self):
        # Обработчики полей ждут массив файлов — как от <input multiple>.
        self.assertIn('onDone={(file) => { setScanning(false); onFiles([file]); }}', self.UI)

    def test_file_picker_opens_in_the_same_tap(self):
        # Safari открывает выбор файла только в том же нажатии — без таймеров.
        self.assertIn("{ key: 'upload', label: 'Загрузить файл', onClick: () => { setSheetOpen(false); pick(); } }", self.UI)
        self.assertIn("{ key: 'upload', label: 'Загрузить файл', icon: FileUp, onSelect: pick }", self.UI)
        body = self.UI[self.UI.index('export const AttachButton'):self.UI.index('export const ACCEPT_DOCS')]
        self.assertNotIn('setTimeout', body)

    def test_focus_returns_to_the_attach_button(self):
        self.assertIn("returnFocus={() => triggerRef.current?.querySelector('button')?.focus({ preventScroll: true })}", self.UI)
        self.assertIn('if (returnFocusRef.current) returnFocusRef.current();', self.SCANNER)

    def test_scanner_uses_the_tested_decisions(self):
        # Решения вынесены в docScan.js и покрыты node-тестом; окно их только зовёт.
        for call in ("scanOutputType(encoded.length, formats) === 'jpeg'", 'for (const { quality, maxSide } of SCAN_QUALITY_STEPS)',
                     'keepScannedPage(pages, shot, `p${pageSeq}`)', 'scannerBack({ step, pages: pages.length, busy, confirm: confirmDiscard })',
                     'cropUsable(shot.corners, shot.width, shot.height)', 'turnedSize(size.width, size.height, page.rotation)'):
            self.assertIn(call, self.SCANNER)

    def test_white_on_dark_steps_survives_the_dark_layer(self):
        # Тёмный слой портала перекрашивает .bg-white и .text-slate-900 — у
        # спуска, «Оставить», вспышки и кнопки в ошибке камеры белый задан иначе.
        theme = (ROOT / 'src' / 'theme-dark.css').read_text(encoding='utf-8')
        self.assertIn('html[data-otp-theme="dark"] .bg-white {', theme)
        for marker in ('rounded-full bg-[#fff] transition active:scale-90',
                       'rounded-full bg-[#fff] px-5 py-2 text-[15px] font-semibold text-[#0f172a]',
                       'mt-1 rounded-full bg-[#fff] px-4 py-2 text-[15px] font-semibold text-[#0f172a]',
                       'absolute inset-0 bg-[#fff] opacity-0'):
            self.assertIn(marker, self.SCANNER)

    def test_blank_frame_is_not_captured(self):
        self.assertIn("if (!track || track.readyState !== 'live' || track.muted) {", self.SCANNER)
        self.assertIn("setCamera({ state: 'error', message: 'Камера не даёт кадр' });", self.SCANNER)
        self.assertIn('if (frameIsBlank(frame)) {', self.SCANNER)
