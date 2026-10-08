// Удалённый КЦ: рядовому сотруднику отдела портал показывает только «Профиль»,
// «Мои смены» и «Вики».
//
// Решение владельца 08.10.2026: «у операторов отдела удалённый КЦ должны
// отображаться лишь раздел профиль, мои смены и вики, который не доступен без
// сканирования QR». Набор «только это» выдан отделу и роли, а не человеку, и
// строже карты отдела: в нём нет ни общих «Ивентов», ни «Библиотеки».
//
// Проверяем поведение предикатов и настоящих стражей App.jsx, а не текст файла:
// карта из литералов, и опечатка в коде отдела синтаксис не ломает — она молча
// возвращает оператору всё меню.
//
// Запуск: node --test tests/remote_cc_department_views.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    BACK_OFFICE_EMPLOYEE_ROLES,
    departmentAllowsView,
    departmentCodeEmployeeRole,
    departmentCodeHidesEmployeeDirection,
    departmentCodeHidesEmployeeSip,
    departmentCodeHidesEmployeeSipInput,
    departmentCodeHidesOperatorFields,
    departmentRestrictsViews,
    employeeRoleForDepartmentCode,
    firstAllowedView,
    personalViewsAllow,
    personalViewsOf,
} from '../src/utils/departmentViews.js';
import { headedDepartmentId, headedDepartmentsOf, isDepartmentHead, normalizeRole } from '../src/utils/roles.js';
import {
    HEAD_CANDIDATES_LIMIT, mergePeople, pickHeadCandidates, roleLabel,
} from '../src/components/departments/headCandidates.js';

const readLf = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const appSource = readLf('src/App.jsx');

const REMOTE = 1954;
const employee = (overrides = {}) => ({
    id: 700, role: 'operator', department_code: 'remote_cc', department_id: REMOTE, ...overrides,
});

// Всё, что портал показал бы оператору отдела без набора, и общее.
const HIDDEN_VIEWS = [
    'events', 'library', 'hours', 'shift_auction', 'evaluation', 'ai_feedback', 'surveys',
    'salary', 'contests', 'tasks', 'lms', 'call_evaluation', 'call_division', 'manage_operators',
    'qr_access', 'driver_mailings', 'baiga', 'dial_list',
];

test('оператору удалённого КЦ остаются «Профиль», «Мои смены» и «Вики»', () => {
    const user = employee();
    assert.deepEqual(personalViewsOf(user), ['profile', 'work_schedules', 'wiki']);
    assert.equal(departmentRestrictsViews(user), true);
    assert.equal(departmentAllowsView(user, 'profile'), true);
    assert.equal(departmentAllowsView(user, 'work_schedules'), true);
    // «Вики» набор не прячет…
    assert.equal(personalViewsAllow(user, 'wiki'), true);
    for (const viewKey of HIDDEN_VIEWS) {
        assert.equal(departmentAllowsView(user, viewKey), false, viewKey);
        assert.equal(personalViewsAllow(user, viewKey), false, viewKey);
    }
    // Раздел по умолчанию — «Профиль»: туда человек попадает после входа.
    assert.equal(firstAllowedView(user, []), 'profile');
    assert.equal(firstAllowedView(user, ['hours', 'salary']), 'profile');
});

test('«Вики» в наборе значит «не прятать»: открыть раздел набор не может', () => {
    /* Раздел выдаёт тумблер отдела вместе с пространством вики, и гард «Этап 10»
       спрашивает о нём свой флаг. Ответь карта «да» — человек остался бы в вике,
       которую его отделу выключили. То же с «Библиотекой»: её открывает роль. */
    assert.equal(departmentAllowsView(employee(), 'wiki'), false);
    assert.equal(departmentAllowsView(employee(), 'library'), false);
    assert.equal(departmentAllowsView(employee({ role: 'trainee' }), 'wiki'), false);
    // У тех, кого набор не касается, ответ прежний.
    assert.equal(departmentAllowsView({ id: 5, role: 'operator', department_code: 'szov' }, 'wiki'), true);
    assert.equal(departmentAllowsView({ id: 6, role: 'operator', department_code: 'tez' }, 'wiki'), false);
});

test('стажёру удалённого КЦ — то же, но без «Вики»', () => {
    // Вику запирает QR, а стажёру QR не выдают: раздел открывался бы без подтверждения.
    const trainee = employee({ role: 'trainee' });
    assert.deepEqual(personalViewsOf(trainee), ['profile', 'work_schedules']);
    assert.equal(personalViewsAllow(trainee, 'wiki'), false);
    assert.equal(personalViewsAllow(trainee, 'library'), false);
    assert.equal(departmentAllowsView(trainee, 'events'), false);
    assert.equal(firstAllowedView(trainee, []), 'profile');
});

test('набор — только рядовому: глава, супервайзер, тренер и админы без него', () => {
    for (const role of ['sv', 'supervisor', 'trainer', 'admin', 'super_admin']) {
        const user = employee({ role });
        assert.equal(personalViewsOf(user), null, role);
        assert.equal(departmentRestrictsViews(user), false, role);
        assert.equal(departmentAllowsView(user, 'events'), true, role);
        assert.equal(personalViewsAllow(user, 'library'), true, role);
    }
    // Глава отдела — при любой базовой роли, и своего отдела, и чужого.
    for (const role of ['operator', 'trainee', 'sv', 'admin']) {
        for (const headed of [REMOTE, 1]) {
            const head = employee({ role, headed_department_id: headed });
            assert.equal(personalViewsOf(head), null, `${role}/${headed}`);
            assert.equal(departmentAllowsView(head, 'manage_operators'), true, `${role}/${headed}`);
        }
    }
    // Должности бэк-офиса в набор отдела не вписаны — ограничений им нет.
    for (const role of BACK_OFFICE_EMPLOYEE_ROLES) {
        assert.equal(personalViewsOf(employee({ role })), null, role);
    }
});

test('набор не задел другие отделы и читает код отдела как сервер', () => {
    for (const code of ['szov', 'op', 'tez', 'front_office', 'analytik', 'hr', 'accounting', 'marketing',
        'request_processing_department', 'remote', 'remote_cc2', 'remotecc', null, undefined, '']) {
        assert.equal(personalViewsOf(employee({ department_code: code })), null, String(code));
    }
    // Регистр и пробелы по краям — не другой отдел.
    for (const code of ['Remote_CC', 'REMOTE_CC', ' remote_cc ']) {
        assert.deepEqual(personalViewsOf(employee({ department_code: code })),
            ['profile', 'work_schedules', 'wiki'], code);
    }
    // Имена из прототипа — не строки карты.
    for (const code of ['constructor', '__proto__', 'toString', 'hasOwnProperty']) {
        assert.equal(personalViewsOf(employee({ department_code: code })), null, code);
    }
    // Карты отделов работают по-прежнему: у фронт-офиса «Ивенты» остаются.
    const frontOffice = { id: 7, role: 'operator', department_code: 'front_office' };
    assert.equal(departmentAllowsView(frontOffice, 'events'), true);
    assert.equal(personalViewsAllow(frontOffice, 'library'), true);
});

test('личный набор человека сильнее набора его отдела', () => {
    // 540 — сотрудник с личным набором «Списки Байги»: переведи его в удалённый
    // КЦ — останется при своём наборе.
    assert.deepEqual(personalViewsOf(employee({ id: 540 })), ['baiga']);
    assert.equal(personalViewsAllow(employee({ id: 540 }), 'wiki'), false);
});

/* Объявление `const NAME ...;` из App.jsx целиком — App.jsx монолитен и не
   импортируется, а переписать реестр в тест значило бы проверять копию. */
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

test('меню рядового: из ролевых пунктов остаются два', () => {
    /* Ветка рядового в сайдбаре — её настоящая разметка: каждый пункт стоит под
       departmentAllowsView(user, '<раздел>'). Считаем, какие из них увидит
       оператор удалённого КЦ. */
    const start = appSource.indexOf('{isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead && (');
    const end = appSource.indexOf('{/* Запрещающий список', start);
    assert.ok(start >= 0 && end > start, 'ветка рядового не найдена — проверь тест');
    const branch = appSource.slice(start, end);
    const keys = new Set([...branch.matchAll(/departmentAllowsView\(user, '([a-z_]+)'\)/g)].map((m) => m[1]));
    assert.ok(keys.size >= 9, `пунктов в ветке слишком мало: ${[...keys]}`);
    for (const role of ['operator', 'trainee']) {
        const shown = [...keys].filter((key) => departmentAllowsView(employee({ role }), key)).sort();
        assert.deepEqual(shown, ['profile', 'work_schedules'], role);
    }
    // «Рассылки» в этой ветке стоят под своим предикатом — набор их не выдаёт.
    assert.ok(branch.includes('{canAccessDriverMailings && ('));
});

test('«Вики», «Библиотека» и «Ивенты» читают набор теми же флагами, что и раньше', () => {
    assert.ok(appSource.includes("const wikiSectionEnabled = wikiEnabledFor(user) && personalViewsAllow(user, 'wiki');"));
    assert.ok(appSource.includes("const canAccessLibrarySection = canAccessLibrarySectionForUser(user)\n"
        + "                && personalViewsAllow(user, 'library');"));
    assert.ok(appSource.includes("const eventsSectionShown = departmentAllowsView(user, 'events');"));
    // Замок QR у вики держит должность: оператора спрашивают, стажёра — нет.
    const gated = new Function(`${declarationOf('SENSITIVE_QR_GATED_ROLES')} return SENSITIVE_QR_GATED_ROLES;`)();
    assert.equal(gated.has('operator'), true);
    assert.equal(gated.has('trainee'), false);
    assert.ok(appSource.includes('{view === "wiki" && (sensitiveSectionsLocked ? ('));
    assert.ok(appSource.includes('const sensitiveSectionsLocked = sensitiveSectionQrRequiredFor(user) && !sensitiveAccess.granted;'));
});

/* ДВА СТРАЖА РАЗДЕЛА — ИХ НАСТОЯЩИЙ КОД (тот же приём, что в
   personal_view_allowlist.test.mjs): тела эффектов берём из App.jsx как есть и
   исполняем; незнакомый флаг доступа читается как «нет». */
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
const TRAINER_VIEWS = new Function(`${declarationOf('TRAINER_ALLOWED_VIEWS')} return TRAINER_ALLOWED_VIEWS;`)();

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

/* Куда приходит человек из раздела start. access.wiki — тумблер отдела вместе с
   пространством (wikiEnabledFor), access.library — роль; оба флага дальше
   спрашивают набор, ровно как в App.jsx. */
const settle = (user, start, access = {}) => {
    const role = normalizeRole(user?.role);
    const head = isDepartmentHead(user);
    const flags = {
        isAdminLikeRole: (role === 'admin' && !head) || role === 'super_admin',
        isDepartmentHeadUser: head,
        isPlainTrainer: role === 'trainer' && !head,
        wikiSectionEnabled: Boolean(access.wiki) && personalViewsAllow(user, 'wiki'),
        canAccessLibrarySection: Boolean(access.library) && personalViewsAllow(user, 'library'),
    };
    let view = start;
    const trail = [view];
    for (let pass = 0; pass < 12; pass += 1) {
        let next = null;
        const scope = {
            user, view, isAuthInitializing: false, TRAINER_ALLOWED_VIEWS: TRAINER_VIEWS,
            EMPLOYEE_ACCOUNTING_MANAGER_MENU: [],
            departmentAllowsView, departmentRestrictsViews, firstAllowedView, isDepartmentHead,
            isSupervisorRole: (value) => normalizeRole(value) === 'sv',
            redirectToView: (target) => { if (target && target !== view) next = target; },
            ...flags,
        };
        runEffect(ROLE_GUARD, scope);
        runEffect(VIEW_MAP_GUARD, scope);
        if (next === null) return { view, trail, settled: true };
        view = next;
        trail.push(view);
    }
    return { view, trail, settled: false };
};

const STARTS = ['hours', 'events', 'library', 'surveys', 'salary', 'tasks', 'lms', 'four_you',
    'evaluation', 'contests', 'shift_auction', 'ai_feedback', 'manage_operators', 'baiga', 'dial_list',
    'crm_tickets', 'complaints', 'sip_settings'];

test('стражи: из любого чужого раздела оператор приходит в «Профиль» и остаётся', () => {
    for (const role of ['operator', 'trainee']) {
        for (const wiki of [true, false]) {
            for (const start of STARTS) {
                const result = settle(employee({ role }), start, { wiki, library: true });
                assert.equal(result.settled, true, `${start}: переброс не остановился: ${result.trail.join(' → ')}`);
                assert.equal(result.view, 'profile', `${role}/${start}: ${result.trail.join(' → ')}`);
                assert.ok(result.trail.length <= 3, `${start}: лишние перебросы: ${result.trail.join(' → ')}`);
            }
        }
    }
    // Вход: общий для рядового — в «Мои часы», оттуда тем же проходом в «Профиль».
    assert.deepEqual(settle(employee(), 'hours', { wiki: true }).trail, ['hours', 'profile']);
});

test('стражи: в своих трёх разделах оператор стоит на месте', () => {
    for (const start of ['profile', 'work_schedules']) {
        for (const role of ['operator', 'trainee']) {
            assert.deepEqual(settle(employee({ role }), start, { wiki: true, library: true }).trail, [start],
                `${role}/${start}`);
        }
    }
    assert.deepEqual(settle(employee(), 'wiki', { wiki: true }).trail, ['wiki']);
});

test('стражи: вику, выключенную отделу, набор не открывает', () => {
    // Тумблер отдела выключен или отдел не включён ни в одно пространство вики.
    assert.deepEqual(settle(employee(), 'wiki', { wiki: false }).trail, ['wiki', 'profile']);
    // Стажёру вика не выдана набором, даже когда отделу она включена.
    assert.deepEqual(settle(employee({ role: 'trainee' }), 'wiki', { wiki: true }).trail, ['wiki', 'profile']);
    // «Библиотеку» роль даёт, набор — нет.
    assert.deepEqual(settle(employee(), 'library', { wiki: true, library: true }).trail, ['library', 'profile']);
});

test('стражи: глава и супервайзер удалённого КЦ ходят как раньше', () => {
    const head = employee({ role: 'admin', headed_department_id: REMOTE });
    const supervisor = employee({ role: 'sv' });
    for (const start of ['events', 'library', 'wiki']) {
        assert.deepEqual(settle(head, start, { wiki: true, library: true }).trail, [start], `глава/${start}`);
        assert.deepEqual(settle(supervisor, start, { wiki: true, library: true }).trail, [start], `СВ/${start}`);
    }
});

const { pickMobileTabs } = new Function(
    `${['MOBILE_TAB_SECTIONS', 'MOBILE_TAB_LIMIT', 'pickMobileTabs'].map(declarationOf).join('\n')}
     return { pickMobileTabs };`,
)();

test('бар телефона: у оператора в нём только «Мои смены»', () => {
    /* Флаги — те же выражения, что в mobileTabItems (App.jsx): личные разделы
       рядового стоят под departmentAllowsView, «Вики» и «Ивенты» — под флагами
       набора. Бар собирается только из личных разделов (pickMobileTabs), поэтому
       «Вики» у оператора — в шторке меню, как у всех рядовых. */
    const user = employee();
    const access = {
        myHours: departmentAllowsView(user, 'hours'),
        myShifts: departmentAllowsView(user, 'work_schedules'),
        myShiftAuction: departmentAllowsView(user, 'shift_auction'),
        myEvaluations: departmentAllowsView(user, 'evaluation'),
        qrAccess: false,
        wiki: personalViewsAllow(user, 'wiki'),
        lms: false,
        events: departmentAllowsView(user, 'events'),
        groupLate: false,
        tasks: false,
        workSchedules: departmentAllowsView(user, 'work_schedules'),
        surveys: departmentAllowsView(user, 'surveys'),
    };
    assert.equal(access.wiki, true);
    assert.deepEqual(pickMobileTabs(access, {}).map((tab) => tab.view), ['work_schedules']);
    // Стажёр: то же, и «Вики» ему не выдана набором.
    const trainee = employee({ role: 'trainee' });
    assert.equal(personalViewsAllow(trainee, 'wiki'), false);
    assert.equal(departmentAllowsView(trainee, 'work_schedules'), true);
});

test('в карточке сотрудника удалённого КЦ нет поля «SIP номер»', () => {
    /* Номер у отдела — линия Binotel, её выдают в разделе «Удаленный КЦ» вместе с
       учёткой линии. Остальное в карточке остаётся: группа, направление и
       колонка SIP в списке (там виден номер выданной линии). */
    for (const code of ['remote_cc', 'Remote_CC', ' remote_cc ']) {
        assert.equal(departmentCodeHidesEmployeeSipInput(code), true, code);
    }
    assert.equal(departmentCodeHidesOperatorFields('remote_cc'), false);
    assert.equal(departmentCodeHidesEmployeeDirection('remote_cc'), false);
    assert.equal(departmentCodeHidesEmployeeSip('remote_cc'), false);
    // Отделы с линией поле не потеряли.
    for (const code of ['szov', 'op', 'tez', 'front_office', 'analytik', null, '']) {
        assert.equal(departmentCodeHidesEmployeeSipInput(code), false, String(code));
    }
});

test('профиль: список возглавляемых отделов — в порядке сервера, первый — отдел по умолчанию', () => {
    const head = {
        id: 300, role: 'admin', department_code: 'szov', headed_department_id: 1,
        headed_departments: [
            { id: 1, name: 'СЗоВ', code: 'SZOV' },
            { id: String(REMOTE), name: 'Удаленный КЦ', code: ' remote_cc ' },
        ],
    };
    assert.deepEqual(headedDepartmentsOf(head), [
        { id: 1, name: 'СЗоВ', code: 'szov' },
        { id: REMOTE, name: 'Удаленный КЦ', code: 'remote_cc' },
    ]);
    assert.equal(headedDepartmentsOf(head)[0].id, headedDepartmentId(head));
    // Отдел без кода в списке остаётся — выбрать его можно.
    assert.deepEqual(headedDepartmentsOf({ headed_departments: [{ id: 7, name: 'Без кода', code: '' }] }),
        [{ id: 7, name: 'Без кода', code: null }]);
});

test('профиль без списка отделов — выбора нет', () => {
    // Профиль, сохранённый до появления поля, и мусор вместо списка.
    for (const profile of [null, undefined, {}, { headed_department_id: 1 }, { headed_departments: null },
        { headed_departments: 'szov' }, { headed_departments: {} }]) {
        assert.deepEqual(headedDepartmentsOf(profile), []);
    }
    // Строки без id отбрасываются, остальное остаётся.
    assert.deepEqual(headedDepartmentsOf({
        headed_departments: [null, 'x', {}, { id: '' }, { id: null }, { id: 'abc' }, { id: 5, name: 'Отдел' }],
    }), [{ id: 5, name: 'Отдел', code: null }]);
    // Запасное имя поля — как у остальных полей профиля.
    assert.deepEqual(headedDepartmentsOf({ headedDepartments: [{ id: 5, name: 'Отдел', code: 'op' }] }),
        [{ id: 5, name: 'Отдел', code: 'op' }]);
});

/* ПРЕЖНИЙ «ПРОФИЛЬ» — НАСТОЯЩИЙ КОМПОНЕНТ (тот же приём, что в
   custom_select_render.test.mjs: esbuild + react-dom/server). Удалённому КЦ
   достался прежний вид профиля, а в нём «Быстрые действия» ведут в «Мои часы» и
   «Мои оценки» — в разделы, которых у оператора отдела нет. */
const loadLegacyProfile = async () => {
    const { createRequire } = await import('node:module');
    const require = createRequire(import.meta.url);
    const { transformSync } = require('esbuild');
    const { writeFileSync, mkdirSync } = require('node:fs');
    const { join } = require('node:path');
    const jsx = readLf('src/components/profile/LegacyProfileView.jsx');
    const { code } = transformSync(jsx, { loader: 'jsx', format: 'esm', target: 'node18' });
    // Значок и шапка телефона к проверяемому не относятся — подменяем.
    const patched = code
        .replace(/import\s+FaIcon\s+from\s*['"][^'"]+['"];?/, 'const FaIcon = () => null;')
        .replace(/import\s+MobileScrollTitle\s+from\s*['"][^'"]+['"];?/, 'const MobileScrollTitle = () => null;');
    assert.ok(!/from\s*['"]\.\.\//.test(patched), 'у компонента появился новый импорт — допиши подмену');
    const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
    mkdirSync(dir, { recursive: true });
    const file = join(dir, 'LegacyProfileView.mjs');
    writeFileSync(file, patched, 'utf8');
    const React = require('react');
    const { renderToStaticMarkup } = require('react-dom/server');
    const Component = (await import(`file://${file.split('\\').join('/')}`)).default;
    return (props) => renderToStaticMarkup(React.createElement(Component, {
        loading: false,
        profile: { name: 'Сотрудник', hire_date: '2026-09-01', direction: 'Обзвон' },
        isMobileShell: false,
        hidesOperatorBlocks: false,
        onOpenView: () => {},
        ...props,
    }));
};

test('прежний «Профиль»: кнопок в разделы, которых у человека нет, не показываем', async () => {
    const render = await loadLegacyProfile();
    const actions = (html) => ['Быстрые действия', 'Мои часы', 'Мои оценки'].map((text) => html.includes(text));
    // По умолчанию — обе кнопки, как было всегда.
    assert.deepEqual(actions(render({})), [true, true, true]);
    // Оператор удалённого КЦ: ни «Моих часов», ни «Моих оценок» — нет и блока.
    const allowed = ['hours', 'evaluation'].filter((viewKey) => departmentAllowsView(employee(), viewKey));
    assert.deepEqual(allowed, []);
    assert.deepEqual(actions(render({ quickViews: allowed })), [false, false, false]);
    // Раздел выдан один — кнопка одна.
    assert.deepEqual(actions(render({ quickViews: ['hours'] })), [true, true, false]);
    assert.deepEqual(actions(render({ quickViews: ['evaluation'] })), [true, false, true]);
    // Отдел без набора и карты разделов — обе кнопки на месте.
    const free = ['hours', 'evaluation'].filter((viewKey) => departmentAllowsView(
        { id: 9, role: 'operator', department_code: 'analytik' }, viewKey));
    assert.deepEqual(actions(render({ quickViews: free })), [true, true, true]);
    // Профиль остаётся профилем: имя и карточки на месте в любом случае.
    assert.ok(render({ quickViews: [] }).includes('Стаж работы'));
});

test('прежний «Профиль»: App передаёт кнопки по карте разделов', () => {
    assert.ok(appSource.includes("const quickViews = ['hours', 'evaluation']\n"
        + '                                      .filter((viewKey) => departmentAllowsView(user, viewKey));'));
    assert.ok(appSource.includes(
        'return <LegacyProfileView {...profileViewProps} onOpenView={setView} quickViews={quickViews} />;'));
});

/* ЗАГРУЗКА ГРУПП ДЛЯ КАРТОЧКИ — НАСТОЯЩЕЕ ТЕЛО fetchUserModalGroups из App.jsx,
   с подменёнными axios и сеттерами состояния. */
const fetchGroupsBody = (() => {
    const opener = 'const fetchUserModalGroups = async () => {';
    const start = appSource.indexOf(opener);
    const end = appSource.indexOf('const saveDirections = async', start);
    assert.ok(start >= 0 && end > start, 'fetchUserModalGroups не найден — проверь тест');
    const body = appSource.slice(start + opener.length, end);
    return body.slice(0, body.lastIndexOf('};'));
})();

const GROUPS_BY_DEPARTMENT = {
    1: [{ id: 10, name: 'Линия 1', department_id: 1 }, { id: 11, name: 'Линия 2', department_id: 1 }],
    [REMOTE]: [{ id: 90, name: 'Удаленный КЦ', department_id: REMOTE }],
};

const runFetchGroups = async (user, { firstHeaded = 1, accountingManager = false, failOther = false, failFirst = false } = {}) => {
    const requests = [];
    const state = { groups: 'не трогали', other: [{ id: 777, name: 'чужая', department_id: 5 }] };
    const head = isDepartmentHead(user) && normalizeRole(user.role) !== 'super_admin';
    const scope = new Proxy({
        user,
        API_BASE_URL: 'http://api',
        isMounted: { current: true },
        isScopedDepartmentHead: head,
        scopedDepartmentId: head ? headedDepartmentId(user) : null,
        isEmployeeAccountingManager: accountingManager,
        headedDepartmentsOf,
        withAccessTokenHeader: (headers) => headers,
        console: { error: () => {} },
        axios: {
            get: async (url, config = {}) => {
                const departmentId = config.params ? config.params.department_id : null;
                requests.push({ url, departmentId });
                if (departmentId == null && failFirst) throw new Error('сеть');
                if (departmentId != null && failOther) throw new Error('сеть');
                return { data: { status: 'success', groups: GROUPS_BY_DEPARTMENT[departmentId ?? firstHeaded] || [] } };
            },
        },
        setUserModalGroups: (value) => { state.groups = value; },
        setUserModalOtherDepartmentGroups: (value) => {
            state.other = typeof value === 'function' ? value(state.other) : value;
        },
    }, { has: () => true, get: (target, key) => (key === Symbol.unscopables ? undefined : (key in target ? target[key] : globalThis[key])) });
    // eslint-disable-next-line no-new-func
    await new Function('scope', `with (scope) { return (async () => {${fetchGroupsBody}\n})(); }`)(scope);
    return { requests, state };
};

const twoHeadProfile = {
    id: 300, role: 'admin', department_code: 'szov', headed_department_id: 1,
    headed_departments: [{ id: 1, name: 'СЗоВ', code: 'szov' }, { id: REMOTE, name: 'Удаленный КЦ', code: 'remote_cc' }],
};

test('группы для карточки: глава двух отделов получает группы и второго отдела — отдельным списком', async () => {
    const { requests, state } = await runFetchGroups(twoHeadProfile);
    // Первый запрос — прежний, без параметра; второй — за группами второго отдела.
    assert.deepEqual(requests, [
        { url: 'http://api/api/groups', departmentId: null },
        { url: 'http://api/api/groups', departmentId: REMOTE },
    ]);
    // Список массового перевода — только первый отдел, как раньше.
    assert.deepEqual(state.groups.map((group) => group.id), [10, 11]);
    assert.deepEqual(state.other.map((group) => group.id), [90]);
});

test('группы для карточки: у всех остальных запрос один, чужой список очищается', async () => {
    const oneHead = { ...twoHeadProfile, headed_departments: [{ id: 1, name: 'СЗоВ', code: 'szov' }] };
    const { headed_departments: _dropped, ...oldProfile } = twoHeadProfile;
    const cases = [
        ['глава одного отдела', oneHead, {}],
        ['профиль без списка отделов', oldProfile, {}],
        ['супервайзер', { id: 5, role: 'sv', department_code: 'szov' }, {}],
        ['супер-админ, возглавляющий отделы', { ...twoHeadProfile, role: 'super_admin' }, {}],
        ['кадровик во главе отделов', twoHeadProfile, { accountingManager: true }],
    ];
    for (const [name, user, options] of cases) {
        const { requests, state } = await runFetchGroups(user, options);
        assert.deepEqual(requests, [{ url: 'http://api/api/groups', departmentId: null }], name);
        assert.deepEqual(state.groups.map((group) => group.id), [10, 11], name);
        // Группы, оставшиеся от прежнего вошедшего, следующему не достаются.
        assert.deepEqual(state.other, [], name);
    }
});

test('группы для карточки: отказ одного запроса не отнимает ответ другого', async () => {
    const otherFailed = await runFetchGroups(twoHeadProfile, { failOther: true });
    assert.deepEqual(otherFailed.state.groups.map((group) => group.id), [10, 11]);
    const firstFailed = await runFetchGroups(twoHeadProfile, { failFirst: true });
    assert.equal(firstFailed.state.groups, 'не трогали');
    assert.deepEqual(firstFailed.state.other.map((group) => group.id), [90]);
    // Первый её отдел — не обязательно тот, что стоит первым в её профиле по id.
    const { requests } = await runFetchGroups({ ...twoHeadProfile, headed_department_id: REMOTE }, { firstHeaded: REMOTE });
    assert.deepEqual(requests.map((request) => request.departmentId), [null, 1]);
});

test('должность нового сотрудника следует за отделом, выбранным в карточке', () => {
    // Рядовая должность: в бэк-офисе — должность отдела, в отделе с линией — оператор.
    assert.equal(employeeRoleForDepartmentCode('marketing_manager', 'remote_cc'), 'operator');
    assert.equal(employeeRoleForDepartmentCode('hr_manager', 'szov'), 'operator');
    assert.equal(employeeRoleForDepartmentCode('operator', 'marketing'), 'marketing_manager');
    assert.equal(employeeRoleForDepartmentCode('operator', 'hr'), 'hr_manager');
    assert.equal(employeeRoleForDepartmentCode('accounting_manager', 'hr'), 'hr_manager');
    assert.equal(employeeRoleForDepartmentCode('operator', 'remote_cc'), 'operator');
    assert.equal(employeeRoleForDepartmentCode(' Operator ', ' Marketing '), 'marketing_manager');
    // Отдел неизвестен или без кода — бэк-офисная должность в нём не остаётся.
    for (const code of [null, undefined, '', 'нет такого']) {
        assert.equal(employeeRoleForDepartmentCode('marketing_manager', code), 'operator', String(code));
        assert.equal(employeeRoleForDepartmentCode('operator', code), 'operator', String(code));
        assert.equal(employeeRoleForDepartmentCode('', code), 'operator', String(code));
    }
    // Стажёра, тренера, супервайзера и админа отдел не переопределяет.
    for (const role of ['trainee', 'trainer', 'sv', 'admin']) {
        for (const code of ['marketing', 'remote_cc', 'hr', null]) {
            assert.equal(employeeRoleForDepartmentCode(role, code), role, `${role}/${code}`);
        }
    }
    // То же правило, что при отправке: App.jsx считает должность так же.
    for (const [role, code] of [['operator', 'marketing'], ['hr_manager', 'szov'], ['operator', 'szov']]) {
        assert.equal(employeeRoleForDepartmentCode(role, code), departmentCodeEmployeeRole(code) || 'operator');
    }
});

/* ОКНО «ГЛАВА ОТДЕЛА» — настоящие правила списка (headCandidates.js). */
const REMOTE_DEPT = { id: REMOTE, name: 'Удаленный КЦ', head_user_id: null };
const PEOPLE = [
    { id: 300, name: 'Яковлева Руководитель', role: 'admin', status: 'working', department_id: 1 },
    { id: 401, name: 'Петров Оператор', role: 'operator', status: 'working', department_id: REMOTE },
    { id: 402, name: 'Абаев Оператор', role: 'operator', status: 'working', department_id: REMOTE },
    { id: 403, name: 'Уволенный Удалённый', role: 'operator', status: 'fired', department_id: REMOTE },
    { id: 404, name: 'Борисов Линия', role: 'operator', status: 'working', department_id: 1 },
    { id: 405, name: 'Уволенная Линия', role: 'sv', status: 'dismissal', department_id: 1 },
    { id: 406, name: 'Без Отдела', role: 'trainer', status: 'working', department_id: null },
    { id: 21, name: 'Васильева Супервайзер', role: 'sv', status: 'working', department_id: 1 },
];
const DEPARTMENT_NAMES = new Map([[1, 'СЗоВ'], [REMOTE, 'Удаленный КЦ']]);
const departmentNameOf = (person) => DEPARTMENT_NAMES.get(Number(person?.department_id)) || '';
const pickNames = (options) => pickHeadCandidates({
    people: PEOPLE, department: REMOTE_DEPT, departmentNameOf, ...options,
}).shown.map((person) => person.name);

test('окно главы: кандидаты — и не из отдела; свои первыми, дальше по алфавиту', () => {
    assert.deepEqual(pickNames({}), [
        'Абаев Оператор', 'Петров Оператор',
        'Без Отдела', 'Борисов Линия', 'Васильева Супервайзер', 'Яковлева Руководитель',
    ]);
    // Уволенных нет — ни с «fired», ни с «dismissal».
    assert.ok(!pickNames({}).some((name) => name.startsWith('Уволенн')));
    assert.equal(pickHeadCandidates({ people: PEOPLE, department: REMOTE_DEPT }).total, 6);
});

test('окно главы: действующая глава — первой, даже не из отдела и даже уволенная', () => {
    // Иначе в окне, которое её и снимает, её не было бы видно за обрезом списка.
    const outsider = { ...REMOTE_DEPT, head_user_id: 300 };
    assert.equal(pickNames({ department: outsider })[0], 'Яковлева Руководитель');
    assert.deepEqual(pickNames({ department: outsider }).slice(1, 3), ['Абаев Оператор', 'Петров Оператор']);
    const fired = { ...REMOTE_DEPT, head_user_id: '405' };
    assert.equal(pickNames({ department: fired })[0], 'Уволенная Линия');
    assert.ok(!pickNames({ department: fired }).includes('Уволенный Удалённый'));
    // С обрезом в одну строку остаётся именно она.
    assert.deepEqual(pickNames({ department: outsider, limit: 1 }), ['Яковлева Руководитель']);
});

test('окно главы: поиск — по имени, должности и отделу', () => {
    assert.deepEqual(pickNames({ query: ' яков ' }), ['Яковлева Руководитель']);
    assert.deepEqual(pickNames({ query: 'супервайзер' }), ['Васильева Супервайзер']);
    assert.deepEqual(pickNames({ query: 'сзов' }), ['Борисов Линия', 'Васильева Супервайзер', 'Яковлева Руководитель']);
    assert.deepEqual(pickNames({ query: 'удаленный' }), ['Абаев Оператор', 'Петров Оператор']);
    assert.deepEqual(pickNames({ query: 'нет такого' }), []);
    // Без названий отделов поиск по отделу просто ничего не находит.
    assert.deepEqual(pickHeadCandidates({ people: PEOPLE, department: REMOTE_DEPT, query: 'сзов' }).shown, []);
});

test('окно главы: список обрезается, а счёт — по всем подошедшим', () => {
    const crowd = Array.from({ length: 75 }, (_, i) => ({
        id: 1000 + i, name: `Массовка ${String(i).padStart(2, '0')}`, role: 'operator', status: 'working', department_id: 367,
    }));
    const { shown, total } = pickHeadCandidates({ people: [...PEOPLE, ...crowd], department: REMOTE_DEPT });
    assert.equal(HEAD_CANDIDATES_LIMIT, 60);
    assert.equal(shown.length, 60);
    assert.equal(total, 81);
    // Свои и здесь первыми — за обрез уходят чужие.
    assert.deepEqual(shown.slice(0, 2).map((person) => person.name), ['Абаев Оператор', 'Петров Оператор']);
    // Отдел не выбран (окно закрыто) или списка нет — пусто.
    assert.deepEqual(pickHeadCandidates({ people: PEOPLE, department: null }), { shown: [], total: 0 });
    assert.deepEqual(pickHeadCandidates({ people: null, department: REMOTE_DEPT }), { shown: [], total: 0 });
});

test('окно главы: два списка сливаются без повторов', () => {
    const employees = [{ id: 1, name: 'А' }, { id: '2', name: 'Б' }, { name: 'без id' }, null];
    const supervisors = [{ id: 2, name: 'Б из второго списка' }, { id: 3, name: 'В' }, { id: '', name: 'пустой id' }];
    assert.deepEqual(mergePeople(employees, supervisors).map((person) => person.name), ['А', 'Б', 'В']);
    // Отказ одной ручки (не массив) не отнимает ответ другой.
    assert.deepEqual(mergePeople(undefined, supervisors).map((person) => person.id), [2, 3]);
    assert.deepEqual(mergePeople(employees, null).map((person) => person.name), ['А', 'Б']);
    assert.deepEqual(mergePeople(), []);
    // Подписи должностей — для строки кандидата.
    assert.equal(roleLabel('supervisor'), 'Супервайзер');
    assert.equal(roleLabel('accounting_manager'), 'Менеджер бухгалтерии');
    assert.equal(roleLabel(''), '—');
});
