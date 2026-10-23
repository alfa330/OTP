import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { isDepartmentHead, normalizeRole } from '../src/utils/roles.js';
import {
    DEPARTMENT_VIEW_ALLOWLIST,
    departmentAllowsView,
    departmentCodeHidesEmployeeDirection,
    departmentCodeHidesEmployeeInternship,
    departmentCodeHidesEmployeeSip,
    departmentCodeHidesEmployeeSipInput,
    departmentCodeHidesEmployeeTaxiproId,
    departmentCodeHidesFrontOfficeTraining,
    departmentCodeHidesOperatorFields,
    departmentCodeUsesEmployeeJobTitle,
    departmentHidesEmployeeDirection,
    departmentHidesEmployeeInternship,
    departmentHidesEmployeeTaxiproId,
    departmentRestrictsViews,
    firstAllowedView,
} from '../src/utils/departmentViews.js';

/* ООЗ — отдел обработки запросов (задача #359). Сотрудник заводится
   оператором и зачисляется в группу, но видит в портале только «Рассылки»,
   а в карточке у него нет направления, SIP-номера, практики, обучения во
   фронт офисе и ID таксипро. Проверяем поведение предикатов, а не текст
   файла: карта из литералов, и опечатка в коде отдела или ключе роли
   синтаксис не ломает — она молча снимает ограничение целиком. */

const CODE = 'request_processing_department';
const OTHER_CODES = ['szov', 'op', 'tez', 'front_office', 'accounting', 'hr', 'marketing'];

const employee = (role = 'operator', code = CODE) => ({ id: 3, role, department_code: code });
// Глава ООЗ — админ портала с назначением главой, как на проде.
const head = () => ({
    id: 1, role: 'admin', department_code: CODE, department_id: 2008,
    headed_department_id: 2008,
});

for (const role of ['operator', 'trainee']) {
    test(`ООЗ: ${role} видит только «Рассылки»`, () => {
        const user = employee(role);
        assert.equal(departmentRestrictsViews(user), true);
        assert.equal(departmentAllowsView(user, 'driver_mailings'), true);
        for (const denied of [
            'profile', 'hours', 'work_schedules', 'evaluation', 'surveys',
            'salary', 'tasks', 'contests', 'shift_auction', 'ai_feedback',
            'call_evaluation', 'call_division', 'lms', 'manage_operators',
        ]) {
            assert.equal(departmentAllowsView(user, denied), false, denied);
        }
        // «Ивенты» общие для всех ролей — ограничение отдела их не касается.
        assert.equal(departmentAllowsView(user, 'events'), true);
        // Раздел по умолчанию: гард видимости уводит оператора с «Моих часов»
        // именно сюда, а не в «Калькулятор зарплаты».
        assert.equal(firstAllowedView(user, []), 'driver_mailings');
    });
}

test('ООЗ: глава отдела без ограничений — её меню не меняется', () => {
    assert.equal(departmentRestrictsViews(head()), false);
    assert.equal(departmentAllowsView(head(), 'manage_operators'), true);
    assert.equal(DEPARTMENT_VIEW_ALLOWLIST[CODE].head, undefined);
    assert.equal(DEPARTMENT_VIEW_ALLOWLIST[CODE].sv, undefined);
});

test('ООЗ: в карточке нет направления, SIP, практики, обучения ФО и ID таксипро', () => {
    for (const code of [CODE, 'Request_Processing_Department', ` ${CODE} `]) {
        assert.equal(departmentCodeHidesEmployeeDirection(code), true, code);
        assert.equal(departmentCodeHidesEmployeeSipInput(code), true, code);
        assert.equal(departmentCodeHidesEmployeeInternship(code), true, code);
        assert.equal(departmentCodeHidesEmployeeTaxiproId(code), true, code);
        assert.equal(departmentCodeHidesFrontOfficeTraining(code), true, code);
        // Колонка SIP в списке сотрудников — тоже.
        assert.equal(departmentCodeHidesEmployeeSip(code), true, code);
    }
    const user = employee();
    assert.equal(departmentHidesEmployeeDirection(user), true);
    assert.equal(departmentHidesEmployeeInternship(user), true);
    assert.equal(departmentHidesEmployeeTaxiproId(user), true);
});

test('ООЗ: группа в карточке остаётся, «Должность» появляется', () => {
    // Группа живёт под departmentCodeHidesOperatorFields: сними его — и
    // сотрудника снова нельзя было бы зачислить в группу.
    assert.equal(departmentCodeHidesOperatorFields(CODE), false);
    assert.equal(departmentCodeUsesEmployeeJobTitle(CODE), true);
});

test('ООЗ не задел остальные отделы', () => {
    for (const code of OTHER_CODES) {
        assert.equal(departmentCodeHidesEmployeeDirection(code), false, code);
        assert.equal(departmentCodeHidesEmployeeSipInput(code), false, code);
        assert.equal(departmentCodeHidesEmployeeInternship(code), false, code);
        assert.equal(departmentCodeHidesEmployeeTaxiproId(code), false, code);
    }
    // Отдел не выбран («Все отделы») — общие поля на месте.
    for (const code of [null, undefined, '']) {
        assert.equal(departmentCodeHidesEmployeeInternship(code), false);
        assert.equal(departmentCodeHidesEmployeeTaxiproId(code), false);
        assert.equal(departmentCodeHidesEmployeeDirection(code), false);
    }
    // Оператор линии (СЗоВ) по-прежнему без ограничений разделов.
    assert.equal(departmentRestrictsViews(employee('operator', 'szov')), false);
    assert.equal(departmentAllowsView(employee('operator', 'szov'), 'profile'), true);
});

/* «Мои смены» двум сотрудницам ООЗ (users.id 619 и 620) — поимённо, сверх
   карты отдела. Решение владельца 23.10.2026: «открой доступ к разделам
   рассылки и к их графикам», «Мои смены» — только им двоим. Остальным
   сотрудникам отдела портал не меняется. */
const NAMED_IDS = [619, 620];
const named = (id, overrides = {}) => ({
    id, role: 'operator', department_code: CODE, department_id: 2008, ...overrides,
});

for (const id of NAMED_IDS) {
    test(`ООЗ: сотруднице ${id} сверх «Рассылок» открыты «Мои смены»`, () => {
        // id в профиле бывает строкой.
        for (const user of [named(id), named(String(id))]) {
            assert.equal(departmentRestrictsViews(user), true);
            assert.equal(departmentAllowsView(user, 'driver_mailings'), true);
            assert.equal(departmentAllowsView(user, 'work_schedules'), true);
            assert.equal(departmentAllowsView(user, 'events'), true);
            for (const denied of [
                'profile', 'hours', 'evaluation', 'surveys', 'salary', 'tasks', 'contests',
                'shift_auction', 'ai_feedback', 'call_evaluation', 'call_division', 'lms',
                'manage_operators', 'sv_hours',
            ]) {
                assert.equal(departmentAllowsView(user, denied), false, denied);
            }
            // После входа — по-прежнему «Рассылки»: добавка встаёт в хвост набора.
            assert.equal(firstAllowedView(user, []), 'driver_mailings');
        }
    });
}

test('ООЗ: «Мои смены» — только названным, а не всему отделу', () => {
    for (const id of [523, 3, 476, 618, 621, 6190, null, undefined, '', 'abc', '619abc']) {
        for (const role of ['operator', 'trainee']) {
            const user = named(id, { role });
            assert.equal(departmentAllowsView(user, 'work_schedules'), false, `${role}/${id}`);
            assert.equal(firstAllowedView(user, []), 'driver_mailings', `${role}/${id}`);
        }
    }
    // Карта отдела не тронута: добавка живёт отдельной строкой.
    assert.deepEqual(DEPARTMENT_VIEW_ALLOWLIST[CODE].operator, ['driver_mailings']);
    assert.deepEqual(DEPARTMENT_VIEW_ALLOWLIST[CODE].trainee, ['driver_mailings']);
});

test('ООЗ: добавка не едет за человеком в другой отдел', () => {
    // Карта нового отдела без смен — «Мои смены» туда не переносятся.
    for (const [role, code] of [
        ['operator', 'accounting'], ['accounting_manager', 'accounting'],
        ['operator', 'hr'], ['hr_manager', 'hr'], ['marketing_manager', 'marketing'],
    ]) {
        const user = named(619, { role, department_code: code });
        assert.equal(departmentRestrictsViews(user), true, `${role}@${code}`);
        assert.equal(departmentAllowsView(user, 'work_schedules'), false, `${role}@${code}`);
    }
    // Отдел без карты (СЗоВ) или без отдела — ограничений нет, как раньше.
    for (const code of ['szov', null]) {
        assert.equal(departmentRestrictsViews(named(620, { department_code: code })), false, String(code));
    }
});

test('ООЗ: глава отдела и повышенные — без набора, меню новой роли', () => {
    for (const role of ['operator', 'admin']) {
        const head = named(619, { role, headed_department_id: 2008 });
        assert.equal(departmentRestrictsViews(head), false, role);
        assert.equal(departmentAllowsView(head, 'manage_operators'), true, role);
    }
    for (const role of ['sv', 'trainer', 'admin', 'super_admin']) {
        assert.equal(departmentRestrictsViews(named(620, { role })), false, role);
    }
});

/* Проводка — настоящий код App.jsx: ветка рядового в сайдбаре, бар телефона и
   два стража раздела (тот же приём, что в remote_cc_department_views.test.mjs).
   App.jsx монолитен и не импортируется, а переписать его куски в тест значило
   бы проверять копию. */
const appSource = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const declarationOf = (name) => {
    const at = appSource.indexOf(`const ${name} `);
    assert.ok(at >= 0, `объявление ${name} не найдено — проверь тест`);
    let depth = 0;
    for (let i = at; i < appSource.length; i += 1) {
        const ch = appSource[i];
        if (ch === '(' || ch === '[' || ch === '{') depth += 1;
        else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
        else if (ch === ';' && depth === 0) return appSource.slice(at, i + 1);
    }
    throw new Error(`не нашёл конец объявления ${name} — проверь тест`);
};

test('меню рядового: у названных сотрудниц из пунктов карты — одни «Мои смены»', () => {
    const start = appSource.indexOf('{isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead && (');
    const end = appSource.indexOf('{/* Запрещающий список', start);
    assert.ok(start >= 0 && end > start, 'ветка рядового не найдена — проверь тест');
    const branch = appSource.slice(start, end);
    const keys = new Set([...branch.matchAll(/departmentAllowsView\(user, '([a-z_]+)'\)/g)].map((m) => m[1]));
    assert.ok(keys.size >= 9 && keys.has('work_schedules'), `ветка рядового изменилась: ${[...keys]}`);
    const shown = (user) => [...keys].filter((key) => departmentAllowsView(user, key)).sort();
    for (const id of NAMED_IDS) assert.deepEqual(shown(named(id)), ['work_schedules'], String(id));
    assert.deepEqual(shown(named(523)), []);
    // Пункт рядового в этот раздел подписан «Мои смены».
    const item = branch.slice(branch.indexOf("{departmentAllowsView(user, 'work_schedules') && ("));
    assert.match(item.slice(0, item.indexOf('</li>')), /handleSidebarViewNavigation\(e, 'work_schedules'\)[^]*Мои смены/);
    // «Рассылки» в ветке — под именным предикатом, карта их не выдаёт.
    assert.ok(branch.includes('{canAccessDriverMailings && ('));
});

const { pickMobileTabs } = new Function(
    `${['MOBILE_TAB_SECTIONS', 'MOBILE_TAB_LIMIT', 'pickMobileTabs'].map(declarationOf).join('\n')}
     return { pickMobileTabs };`,
)();

test('бар телефона: названным — «Смены», остальным сотрудникам ООЗ — как раньше', () => {
    /* Флаги — те же выражения, что в mobileTabItems (App.jsx) для рядового. */
    const barOf = (user, wiki) => pickMobileTabs({
        myHours: departmentAllowsView(user, 'hours'),
        myShifts: departmentAllowsView(user, 'work_schedules'),
        myShiftAuction: departmentAllowsView(user, 'shift_auction'),
        myEvaluations: departmentAllowsView(user, 'evaluation'),
        qrAccess: false,
        wiki,
        lms: false,
        events: departmentAllowsView(user, 'events'),
        groupLate: false,
        tasks: departmentRestrictsViews(user) && departmentAllowsView(user, 'tasks'),
        workSchedules: departmentAllowsView(user, 'work_schedules'),
        surveys: departmentAllowsView(user, 'surveys'),
    }, {}).map((tab) => tab.view);
    for (const wiki of [false, true]) {
        for (const id of NAMED_IDS) assert.deepEqual(barOf(named(id), wiki), ['work_schedules'], `${id}/${wiki}`);
        assert.deepEqual(barOf(named(523), wiki), wiki ? ['wiki', 'events'] : ['events'], `523/${wiki}`);
    }
    // Мост к App.jsx: флаг «Смен» в баре спрашивает тот же departmentAllowsView.
    assert.match(appSource, /myShifts: isRankAndFileRole\(currentUserRole\) && !isScopedDepartmentHead\s+&& departmentAllowsView\(user, 'work_schedules'\)/);
});

const effectBody = (anchor, { openBefore, closing }) => {
    const anchorAt = appSource.indexOf(anchor);
    assert.ok(anchorAt >= 0, `эффект не найден: ${anchor}`);
    const opener = 'useEffect(() => {';
    const open = openBefore ? appSource.lastIndexOf(opener, anchorAt) : appSource.indexOf(opener, anchorAt);
    const end = appSource.indexOf(closing, open);
    assert.ok(open >= 0 && end > open, `границы эффекта не найдены: ${anchor}`);
    return appSource.slice(open + opener.length, end);
};

const ROLE_GUARD = effectBody('// Do not touch view while authentication is still initializing', {
    openBefore: true, closing: '\n            }, [isAuthInitializing, user,',
});
const VIEW_MAP_GUARD = effectBody('// Гард видимости разделов по отделу (Этап 10)', {
    openBefore: false, closing: '\n            }, [user?.id, user?.role, user?.department_code',
});

// Незнакомый флаг доступа читается как «нет».
const runEffect = (body, scope) => {
    const everything = new Proxy(scope, {
        has: () => true,
        get: (target, key) => {
            if (key === Symbol.unscopables) return undefined;
            if (key in target) return target[key];
            if (key in globalThis) return globalThis[key];
            return false;
        },
    });
    // eslint-disable-next-line no-new-func
    new Function('scope', `with (scope) {${body}\n}`)(everything);
};

// Куда приходит рядовой из раздела start. «Рассылки» открыты всем троим.
const settle = (user, start) => {
    let view = start;
    const trail = [view];
    for (let pass = 0; pass < 12; pass += 1) {
        let next = null;
        const scope = {
            user, view, isAuthInitializing: false, EMPLOYEE_ACCOUNTING_MANAGER_MENU: [],
            isAdminLikeRole: false, isDepartmentHeadUser: false, isPlainTrainer: false,
            departmentAllowsView, departmentRestrictsViews, firstAllowedView, isDepartmentHead,
            isSupervisorRole: (value) => normalizeRole(value) === 'sv',
            redirectToView: (target) => { if (target && target !== view) next = target; },
            canAccessDriverMailings: true,
        };
        runEffect(ROLE_GUARD, scope);
        runEffect(VIEW_MAP_GUARD, scope);
        if (next === null) return trail;
        view = next;
        trail.push(view);
    }
    assert.fail(`переброс не остановился: ${trail.join(' → ')}`);
};

test('стражи: «Мои смены» названные открывают, вход и чужие разделы ведут в «Рассылки»', () => {
    for (const id of NAMED_IDS) {
        assert.deepEqual(settle(named(id), 'work_schedules'), ['work_schedules'], String(id));
        assert.deepEqual(settle(named(id), 'driver_mailings'), ['driver_mailings'], String(id));
        // Вход рядового — «Мои часы», оттуда тем же проходом в «Рассылки».
        for (const start of ['hours', 'profile', 'evaluation', 'shift_auction', 'salary', 'surveys']) {
            assert.deepEqual(settle(named(id), start), [start, 'driver_mailings'], `${id}/${start}`);
        }
    }
    // Третьего сотрудника «Мои смены» по-прежнему уводят в «Рассылки».
    assert.deepEqual(settle(named(523), 'work_schedules'), ['work_schedules', 'driver_mailings']);
});
