// Временный просмотр «Графиков работы», как у админа, но без правки (владелец, 21.11.2026:
// «Сотрудникам … в сзов надо открыть админский доступ временный к разделам Удаленный КЦ и
// Графики работы чисто на просмотр»).
//
// У оператора «Графики работы» (work_schedules) — его «Мои смены», и отнимать их нельзя.
// Поэтому сетка всех отделов у такого зрителя — свой раздел меню (work_schedules_view): тот же
// экран сетки в режиме «только просмотр», как у тренера. Что он видит и что ему нельзя
// править, решает сервер (tests/test_work_schedule_view_only.py); здесь — флаг, пункт меню,
// экран и то, что в режиме просмотра сетка не уходит ни в одну ветку «оператор → только себя».
//
// Запуск: node --test tests/work_schedules_view_only.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { BACK_OFFICE_EMPLOYEE_ROLES } from '../src/utils/departmentViews.js';
import { headedDepartmentId, isAdminLikeRole, isDepartmentHead, normalizeRole } from '../src/utils/roles.js';

const readLf = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const appSource = readLf('src/App.jsx');

/* Объявление `const NAME ...;` из App.jsx целиком — как в tests/dial_list_section_access.test.mjs. */
const declarationOf = (name) => {
    const at = appSource.indexOf(`const ${name} `);
    assert.ok(at >= 0, `объявление ${name} не найдено — проверь тест`);
    let depth = 0;
    for (let i = at; i < appSource.length; i += 1) {
        const ch = appSource[i];
        const next = appSource[i + 1];
        if (ch === '/' && next === '/') { i = appSource.indexOf('\n', i); continue; }
        if (ch === '/' && next === '*') { i = appSource.indexOf('*/', i) + 1; continue; }
        if (ch === "'" || ch === '"' || ch === '`') {
            let j = i + 1;
            while (j < appSource.length && appSource[j] !== ch) j += appSource[j] === '\\' ? 2 : 1;
            i = j;
            continue;
        }
        if (ch === '(' || ch === '[' || ch === '{') depth += 1;
        else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
        else if (ch === ';' && depth === 0) return appSource.slice(at, i + 1);
    }
    throw new Error(`не нашёл конец объявления ${name} — проверь тест`);
};

const NAMES = ['RANK_AND_FILE_ROLES', 'isRankAndFileRole', 'WORK_SCHEDULE_VIEW_ONLY_USER_IDS',
    'currentUserRole', 'isSuperAdmin', 'isDepartmentHeadUser', 'scopedDepartmentId', 'isScopedDepartmentHead',
    'workSchedulesViewOnly'];
// Настоящий список сейчас пуст (допуск снят 23.11.2026): правило проверяем на выдуманных id —
// объявление списка подменяется в вычислителе; зеркало «сайт = сервер» сверяется с настоящим.
const TEST_VIEWERS = [9170, 9225];
const buildEvaluate = (ids) => new Function(
    'user', 'normalizeRole', 'isDepartmentHead', 'headedDepartmentId', 'isAdminLikeRoleFn', 'BACK_OFFICE_EMPLOYEE_ROLES',
    `${NAMES.map((name) => (ids && name === 'WORK_SCHEDULE_VIEW_ONLY_USER_IDS'
        ? `const WORK_SCHEDULE_VIEW_ONLY_USER_IDS = new Set(${JSON.stringify(ids)});`
        : declarationOf(name))).join('\n')}
     return workSchedulesViewOnly;`,
);
const evaluateReal = buildEvaluate(null);
const evaluateTest = buildEvaluate(TEST_VIEWERS);
const args = [normalizeRole, isDepartmentHead, headedDepartmentId, isAdminLikeRole, BACK_OFFICE_EMPLOYEE_ROLES];
const viewOnly = (user) => evaluateTest(user, ...args);
const viewOnlyReal = (user) => evaluateReal(user, ...args);

const viewers = [...appSource.match(/^const WORK_SCHEDULE_VIEW_ONLY_USER_IDS = new Set\(\[([\d,\s]*)\]\);$/m)[1]
    .matchAll(/\d+/g)].map((m) => Number(m[0]));

const profile = (overrides = {}) => ({
    id: 700, role: 'operator', login: 'op_szov', department_id: 1, department_code: 'szov',
    headed_department_id: null, headed_department_ids: [], headed_department_code: null,
    headed_department_codes: [], ...overrides,
});

test('список людей — тот же, что на сервере', () => {
    const server = readLf('bot_schedule2.py').match(/^WORK_SCHEDULE_VIEW_ONLY_USER_IDS = frozenset\(([^)]*)\)$/m);
    assert.ok(server, 'список на сервере не найден');
    assert.deepEqual([...server[1].matchAll(/\d+/g)].map((m) => Number(m[0])).sort(), [...viewers].sort());
    // По настоящему списку: кого в нём нет — пункта нет.
    for (const id of [170, 225, 700]) {
        if (!viewers.includes(id)) assert.equal(viewOnlyReal(profile({ id })), false, id);
    }
});

test('просмотр — только у рядовых из списка', () => {
    for (const id of TEST_VIEWERS) assert.equal(viewOnly(profile({ id })), true, id);
    assert.equal(viewOnly(profile()), false, 'оператор не из списка');
    // У СВ, админа и главы «Графики работы» и так свои — отдельный пункт им не нужен.
    for (const role of ['sv', 'admin', 'trainer']) {
        assert.equal(viewOnly(profile({ id: TEST_VIEWERS[0], role })), false, role);
    }
    assert.equal(viewOnly(profile({ id: TEST_VIEWERS[0], headed_department_id: 1, headed_department_ids: [1] })), false);
});

test('пункт меню — у рядового, рядом с его «Мои смены», которые остаются', () => {
    const nav = 'handleSidebarViewNavigation(e, WORK_SCHEDULES_VIEW_ONLY_VIEW)';
    const at = appSource.indexOf(nav);
    assert.ok(at > 0, 'пункта нет');
    assert.equal(appSource.indexOf(nav, at + 1), -1, 'пункт один');
    const branch = appSource.lastIndexOf('{isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead && (', at);
    const otherBranch = appSource.lastIndexOf('{isAdminLikeRole && (', at);
    assert.ok(branch > otherBranch, 'пункт — в ветке рядового');
    const gate = appSource.lastIndexOf('{workSchedulesViewOnly && (', at);
    assert.ok(gate > branch && at - gate < 400, 'над пунктом нет своего условия');
    assert.match(appSource.slice(at, at + 600), /<span className="sidebar-text">Графики работы<\/span>/);
    // «Мои смены» рядового на месте.
    assert.ok(appSource.includes("{isHrDepartmentMember ? 'Графики работы' : 'Мои смены'}"));
    assert.ok(appSource.includes("const WORK_SCHEDULES_VIEW_ONLY_VIEW = 'work_schedules_view';"));
});

test('экран: сетка в режиме просмотра, свой экземпляр', () => {
    assert.ok(appSource.includes('{( view === WORK_SCHEDULES_VIEW_ONLY_VIEW && workSchedulesViewOnly && ('));
    assert.match(appSource, /<ShiftPlannerViewWithCalendar key=\{WORK_SCHEDULES_VIEW_ONLY_VIEW\} viewOnlyAll /);
    // Сняли допуск — ссылка ведёт в «Графики работы» роли, а не на пустую страницу.
    assert.ok(appSource.includes("if (!user?.id || view !== WORK_SCHEDULES_VIEW_ONLY_VIEW || workSchedulesViewOnly) return;\n                redirectToView('work_schedules');"));
    // Справочник отделов — для выбора отдела в шапке сетки.
    assert.ok(appSource.includes('} else if (workSchedulesViewOnly) {'));
});

test('правка смен и выходных (владелец, 21.11.2026) — строки операторов СЗоВ, без статусов и «Контроля»', () => {
    // Тот же список, что на сервере (tests/test_work_schedule_shift_editor.py сверяет и его).
    // Список пуст с 23.11.2026 (допуск снят); правило ниже проверяется на выдуманных значениях.
    assert.match(appSource, /^const WORK_SCHEDULE_SHIFT_EDITOR_GRANTS = Object\.freeze\(\{.*\}\);$/m);
    const start = appSource.indexOf('function ShiftPlannerViewWithCalendar(');
    const planner = appSource.slice(start, appSource.indexOf('\n        function startConfetti(', start));
    // Правщик — только в экземпляре просмотра всех графиков.
    assert.ok(planner.includes('const plannerShiftEditCodes = viewOnlyAll ? (WORK_SCHEDULE_SHIFT_EDITOR_GRANTS[Number(user?.id)] || []) : [];'));
    assert.ok(planner.includes('const plannerEditsShifts = !plannerReadOnly || plannerViewerIsShiftEditor;'));

    // Чью строку можно править — настоящее объявление над строками сетки.
    const declarationIn = (source, name) => {
        const at = source.indexOf(`const ${name} = `);
        assert.ok(at >= 0, name);
        return source.slice(at, source.indexOf(';\n', at) + 1);
    };
    const canEdit = new Function('plannerViewerIsShiftEditor', 'user', 'normalizeRole', 'plannerShiftEditCodes',
        'plannerDepartmentCodeById',
        `${declarationIn(planner, 'plannerCanEditShiftsOf')} return plannerCanEditShiftsOf;`);
    const codes = new Map([['1', 'szov'], ['367', 'op']]);
    const editor = canEdit(true, { id: 170 }, normalizeRole, ['szov'], codes);
    assert.equal(editor({ id: 5, role: 'operator', department_id: 1 }), true, 'оператор СЗоВ');
    assert.equal(editor({ id: 5, role: 'sv', department_id: 1 }), false, 'СВ СЗоВ');
    assert.equal(editor({ id: 5, role: 'trainee', department_id: 1 }), false, 'стажёр');
    assert.equal(editor({ id: 5, role: 'operator', department_id: 367 }), false, 'другой отдел');
    assert.equal(editor({ id: 170, role: 'operator', department_id: 1 }), false, 'свои смены');
    assert.equal(editor(null), false);
    assert.equal(canEdit(false, { id: 170 }, normalizeRole, [], codes)({ id: 5, role: 'operator', department_id: 1 }), false);

    // Открыть день и выходной — через замок строки; замок правщика — его правило.
    for (const name of ['openEditModal', 'toggleDayOff', 'openEditModalForMultipleTargets', 'wsOpenDay']) {
        const at = planner.indexOf(`const ${name} = `);
        assert.ok(planner.slice(at, at + 200).includes('if (!plannerEditsShifts) return;'), name);
    }
    assert.ok(planner.includes('? !plannerCanEditShiftsOf(operators.find(o => String(o?.id) === String(opId)))'));
    // Окно дня: только «Смены» и «История»; статус на период не рисуется.
    assert.ok(planner.includes("const plannerShiftEditorTabs = (key) => !plannerViewerIsShiftEditor || key === 'shifts' || key === 'history';"));
    assert.ok(planner.includes('.filter(Boolean).filter(tab => plannerShiftEditorTabs(tab.key)).map(tab => {'));
    assert.ok(planner.includes('].filter(Boolean).filter(tab => plannerShiftEditorTabs(tab.value))}'));
    assert.ok(planner.includes("const modalTabStatus = !plannerViewerIsShiftEditor && (!modalShowTabs || modalActiveTab === 'status');"));
    // Телефон: смены и выходной — по строке окна; статус, «Контроль», штраф — как в просмотре.
    assert.ok(planner.includes('const wsModalShiftsReadOnly = plannerReadOnly && !plannerCanEditShiftsOf(wsModalOperator);'));
    for (const guard of ['{!wsModalShiftsReadOnly ? (\n                                            <WsPhoneGroup label="День">',
        'onEdit={wsModalShiftsReadOnly ? null', 'onRemove={wsModalShiftsReadOnly ? null',
        '{!wsModalShiftsReadOnly && !modalState.isDayOff && wsModalEditing ? (']) {
        assert.ok(planner.includes(guard), guard);
    }
    assert.ok(planner.includes('{!plannerReadOnly && !plannerModalIsNonLineRow && plannerModalCanFine ? ('));
    assert.ok(planner.includes('modalActiveScheduleStatus.isBlacklist && plannerModalCanRemoveBlacklist && !plannerReadOnly ? ('));
});

test('сетка в режиме просмотра — зритель, а не оператор', () => {
    const start = appSource.indexOf('function ShiftPlannerViewWithCalendar(');
    const end = appSource.indexOf('\n        function startConfetti(', start);
    assert.ok(start > 0 && end > start, 'границы экрана сетки не найдены — проверь тест');
    const planner = appSource.slice(start, end);
    assert.ok(planner.startsWith('function ShiftPlannerViewWithCalendar({ initialOperators, user, departments = [], shiftRequestFocus = null, viewOnlyAll = false })'));
    assert.ok(planner.includes("const plannerReadOnly = normalizeRole(user?.role) === 'trainer' || !!viewOnlyAll;"));
    assert.ok(planner.includes("const isOperatorSelfSchedules = user?.role === 'operator' && !viewOnlyAll;"));
    // Ни одной проверки роли оператора в обход флага: иначе экран просмотра молча ушёл бы
    // в ветку «Мои смены» (или не стал бы грузить сетку) — как было бы с 23 местами до правки.
    const stray = planner.split("const isOperatorSelfSchedules = user?.role === 'operator' && !viewOnlyAll;").join('')
        .match(/user\?\.role [!=]== 'operator'/g);
    assert.equal(stray, null, `проверки роли мимо isOperatorSelfSchedules: ${stray}`);
    // Флаг объявлен до первого использования (в эффектах и колбэках ниже).
    assert.ok(planner.indexOf('const isOperatorSelfSchedules') < planner.indexOf('isOperatorSelfSchedules)'));
});
