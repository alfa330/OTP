// «Опросы» у фронт-офисов: глава отдела назначает опросы своим сотрудникам.
//
// Решение владельца 06.10.2026. Раздел выдан двум наборам карты отдела: главе
// (пункт меню руководителя — права на сервере у главы любого отдела уже были)
// и сотруднику (иначе назначенный опрос негде пройти). СВ и стажёр остались
// как были: первого владелец не называл, второго в раздел не пускает сервер.
//
// Проверяем поведение предикатов и настоящий код стражей из App.jsx, а не
// текст карты: набор — литерал, и лишняя или забытая строка синтаксис не ломает.
//
// Запуск: node --test tests/front_office_surveys_views.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    BACK_OFFICE_EMPLOYEE_ROLES,
    DEPARTMENT_VIEW_ALLOWLIST,
    departmentAllowsView,
    departmentCodeEmployeeRole,
    departmentRestrictsViews,
    departmentUsesSimpleEmployeeAccounting,
    firstAllowedView,
} from '../src/utils/departmentViews.js';
import {
    headedDepartmentId, isAdminLikeRole, isDepartmentHead, isSupervisorRole, normalizeRole,
} from '../src/utils/roles.js';

const readLf = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const appSource = readLf('src/App.jsx');

const FRONT_OFFICE_ID = 909;

// Глава отдела, как он заведён на проде: базовая роль admin плюс назначение.
const head = (overrides = {}) => ({
    id: 1, role: 'admin', department_code: 'front_office', department_id: FRONT_OFFICE_ID,
    headed_department_id: FRONT_OFFICE_ID, ...overrides,
});
const supervisor = () => ({
    id: 2, role: 'sv', department_code: 'front_office', department_id: FRONT_OFFICE_ID,
});
const employee = (role = 'operator') => ({
    id: 3, role, department_code: 'front_office', department_id: FRONT_OFFICE_ID,
});

test('глава фронт-офисов: «Опросы» добавлены, остальной набор прежний', () => {
    const user = head();
    assert.equal(departmentRestrictsViews(user), true);
    assert.deepEqual(
        DEPARTMENT_VIEW_ALLOWLIST.front_office.head,
        ['manage_operators', 'groups', 'work_schedules', 'tasks', 'qr_access', 'surveys'],
    );
    for (const allowed of [
        'surveys', 'manage_operators', 'manage_users', 'sv_list', 'groups',
        'work_schedules', 'tasks', 'qr_access', 'events',
    ]) {
        assert.equal(departmentAllowsView(user, allowed), true, allowed);
    }
    for (const denied of [
        'salary', 'sv_hours', 'call_evaluation', 'call_division', 'monitoring_scale', 'ai_qa',
        'trainings', 'technical_issues', 'lms', 'hours', 'profile', 'evaluation',
        'shift_auction', 'contests', 'departments', 'manage_admins',
    ]) {
        assert.equal(departmentAllowsView(user, denied), false, denied);
    }
    // Раздел по умолчанию — по-прежнему учёт сотрудников, а не «Опросы».
    assert.equal(firstAllowedView(user, []), 'manage_operators');
});

test('раздел выдан главенству, а не базовой роли главы', () => {
    for (const role of ['admin', 'sv', 'trainer', 'operator']) {
        assert.equal(departmentAllowsView(head({ role }), 'surveys'), true, role);
    }
    // Снятый с главенства админ отдела — обычный админ: карта его не касается.
    const formerHead = head({ headed_department_id: null });
    assert.equal(departmentRestrictsViews(formerHead), false);
    // Супер-админа карта не касается и при главенстве.
    assert.equal(departmentRestrictsViews(head({ role: 'super_admin' })), false);
});

test('СВ фронт-офисов «Опросы» не получил', () => {
    const user = supervisor();
    assert.deepEqual(
        DEPARTMENT_VIEW_ALLOWLIST.front_office.sv,
        ['manage_operators', 'groups', 'work_schedules'],
    );
    for (const denied of ['surveys', 'tasks', 'qr_access']) {
        assert.equal(departmentAllowsView(user, denied), false, denied);
    }
    assert.equal(firstAllowedView(user, []), 'manage_operators');
});

test('сотрудник фронт-офиса: «Опросы» рядом с профилем и сменами', () => {
    const user = employee();
    assert.deepEqual(
        DEPARTMENT_VIEW_ALLOWLIST.front_office.operator,
        ['profile', 'work_schedules', 'surveys'],
    );
    for (const allowed of ['profile', 'work_schedules', 'surveys', 'events']) {
        assert.equal(departmentAllowsView(user, allowed), true, allowed);
    }
    for (const denied of [
        'hours', 'salary', 'evaluation', 'tasks', 'lms', 'shift_auction', 'contests',
        'manage_operators', 'groups', 'qr_access',
    ]) {
        assert.equal(departmentAllowsView(user, denied), false, denied);
    }
    // 'profile' обязан остаться первым: firstAllowedView берёт allow[0].
    assert.equal(firstAllowedView(user, []), 'profile');
    assert.equal(firstAllowedView(user, ['hours', 'surveys']), 'surveys');
});

test('стажёру фронт-офиса «Опросы» не выданы — сервер его в раздел не пускает', () => {
    const user = employee('trainee');
    assert.deepEqual(DEPARTMENT_VIEW_ALLOWLIST.front_office.trainee, ['profile', 'work_schedules']);
    assert.equal(departmentAllowsView(user, 'surveys'), false);
    assert.equal(departmentAllowsView(user, 'profile'), true);
    assert.equal(departmentAllowsView(user, 'work_schedules'), true);
    assert.equal(firstAllowedView(user, []), 'profile');
    // Наборы сотрудника и стажёра — разные массивы: строка, дописанная одному,
    // не должна приехать второму.
    assert.notEqual(
        DEPARTMENT_VIEW_ALLOWLIST.front_office.trainee,
        DEPARTMENT_VIEW_ALLOWLIST.front_office.operator,
    );
});

test('соседние отделы без линии «Опросов» не получили', () => {
    for (const code of ['accounting', 'hr']) {
        const neighbourHead = { id: 4, role: 'admin', department_code: code, headed_department_id: 42 };
        assert.equal(departmentAllowsView(neighbourHead, 'surveys'), false, `${code}/head`);
        for (const role of ['operator', 'trainee', departmentCodeEmployeeRole(code)]) {
            assert.equal(departmentAllowsView({ id: 5, role, department_code: code }, 'surveys'),
                false, `${code}/${role}`);
        }
    }
    const ooz = { id: 6, role: 'operator', department_code: 'request_processing_department' };
    assert.equal(departmentAllowsView(ooz, 'surveys'), false);
});

/* Объявление `const NAME ...;` из App.jsx целиком — тем же приёмом, что
   mobile_tab_bar.test.mjs и personal_view_allowlist.test.mjs: App.jsx монолитен
   и не импортируется. */
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

/* Два стража раздела — их настоящий код (см. personal_view_allowlist.test.mjs):
   страж ролей и гард «Этап 10». Тела эффектов берём из файла и исполняем;
   флаг доступа, которого профилю не дали, читается как «нет». */
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
const RANK_AND_FILE = new Function(
    'BACK_OFFICE_EMPLOYEE_ROLES', `${declarationOf('RANK_AND_FILE_ROLES')} return RANK_AND_FILE_ROLES;`,
)(BACK_OFFICE_EMPLOYEE_ROLES);

const evaluateIn = (scope, code) => {
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
    return new Function('scope', `with (scope) {${code}\n}`)(everything);
};

/* Флаги, которыми App.jsx описывает человека, — его НАСТОЯЩИЕ объявления,
   исполненные над профилем. Перепиши их тест своими руками — и поломка самого
   объявления (глава перестал быть руководителем отдела) прошла бы мимо него. */
const FLAG_NAMES = ['currentUserRole', 'isSuperAdmin', 'isDepartmentHeadUser', 'scopedDepartmentId',
    'isScopedDepartmentHead', 'isAdminLikeRole', 'isDepartmentManager', 'isPlainTrainer',
    'canUseAdminEmployeeAccounting'];
const computeFlags = new Function(
    'user', 'normalizeRole', 'isDepartmentHead', 'headedDepartmentId', 'isAdminLikeRoleFn', 'isSupervisorRole',
    `${FLAG_NAMES.map(declarationOf).join('\n')}\nreturn { user, ${FLAG_NAMES.join(', ')} };`,
);
const personFlags = (user) => ({
    ...computeFlags(user, normalizeRole, isDepartmentHead, headedDepartmentId, isAdminLikeRole, isSupervisorRole),
    isSupervisorRole,
    isRankAndFileRole: (value) => RANK_AND_FILE.includes(normalizeRole(value)),
    departmentAllowsView,
    departmentRestrictsViews,
});

test('флаги человека из App.jsx: глава — руководитель отдела, а не админ; сотрудник — рядовой', () => {
    const manager = personFlags(head());
    assert.equal(manager.isScopedDepartmentHead, true);
    assert.equal(manager.isDepartmentManager, true);
    assert.equal(manager.isAdminLikeRole, false);
    assert.equal(manager.scopedDepartmentId, FRONT_OFFICE_ID);
    const staff = personFlags(employee());
    assert.equal(staff.isDepartmentManager, false);
    assert.equal(staff.isScopedDepartmentHead, false);
    assert.equal(staff.isRankAndFileRole(staff.currentUserRole), true);
    // Супер-админ, назначенный главой, остаётся админом без карты отдела.
    const root = personFlags(head({ role: 'super_admin' }));
    assert.equal(root.isAdminLikeRole, true);
    assert.equal(root.isScopedDepartmentHead, false);
});

/* Куда приходит человек из раздела start. Один проход — оба эффекта над одним
   view, побеждает последний setView; переход в тот же раздел — не переход. */
const settle = (user, start) => {
    let view = start;
    const trail = [view];
    for (let pass = 0; pass < 12; pass += 1) {
        let next = null;
        const scope = {
            ...personFlags(user), view, isAuthInitializing: false,
            TRAINER_ALLOWED_VIEWS: TRAINER_VIEWS, EMPLOYEE_ACCOUNTING_MANAGER_MENU: [],
            firstAllowedView, isDepartmentHead, departmentUsesSimpleEmployeeAccounting,
            redirectToView: (target) => { if (target && target !== view) next = target; },
        };
        evaluateIn(scope, ROLE_GUARD);
        evaluateIn(scope, VIEW_MAP_GUARD);
        if (next === null) return { view, trail, settled: true };
        view = next;
        trail.push(view);
    }
    return { view, trail, settled: false };
};

test('стражи: глава и сотрудник остаются в «Опросах», СВ и стажёра уводит карта отдела', () => {
    assert.deepEqual(settle(head(), 'surveys').trail, ['surveys']);
    assert.deepEqual(settle(employee(), 'surveys').trail, ['surveys']);
    assert.deepEqual(settle(supervisor(), 'surveys').trail, ['surveys', 'manage_operators']);
    assert.deepEqual(settle(employee('trainee'), 'surveys').trail, ['surveys', 'profile']);
});

test('стражи: вход и чужие разделы у фронт-офисов — как были, без зацикливания', () => {
    const STARTS = ['hours', 'events', 'profile', 'surveys', 'salary', 'tasks', 'lms', 'four_you',
        'work_schedules', 'evaluation', 'contests', 'manage_operators', 'groups', 'qr_access', 'baiga'];
    for (const user of [head(), supervisor(), employee(), employee('trainee')]) {
        for (const start of STARTS) {
            const result = settle(user, start);
            const label = `${user.role}${isDepartmentHead(user) ? '/head' : ''} из ${start}: ${result.trail.join(' → ')}`;
            assert.equal(result.settled, true, `переброс не остановился — ${label}`);
            assert.ok(result.trail.length <= 3, `лишние перебросы — ${label}`);
        }
    }
    // Общий вход рядового — «Мои часы»; у фронт-офиса его уводит в профиль.
    assert.deepEqual(settle(employee(), 'hours').trail, ['hours', 'profile']);
    // Глава из закрытого раздела приходит в учёт сотрудников, а не в «Опросы».
    assert.deepEqual(settle(head(), 'salary').trail, ['salary', 'manage_operators', 'manage_users']);
});

/* Пункты «Опросы» в сайдбаре — все, сколько их есть: условие ролевой ветки и
   собственное условие пункта (у админа и тренера его нет). Условия — обычные
   выражения, поэтому не сверяем их текст, а исполняем над флагами человека. */
const SURVEYS_MENU_ITEMS = [...appSource.matchAll(new RegExp(
    String.raw`\{([^{}\n]+) && \(\s*<>\s*(?:\{([^{}\n]+) && \(\s*)?<li>\s*`
    + String.raw`<button onClick=\{\(e\) => handleSidebarViewNavigation\(e, 'surveys'\)\}`, 'g',
))].map((match) => ({ branch: match[1], own: match[2] || '' }));

const menuItemsShown = (user) => SURVEYS_MENU_ITEMS.filter((item) => evaluateIn(
    personFlags(user), `return Boolean((${item.branch}) && (${item.own || 'true'}));`,
));

test('пункт меню «Опросы»: у главы и сотрудника есть, у СВ и стажёра нет', () => {
    // Опознаны все кнопки раздела: появится ещё одна в другой разметке — тест скажет.
    assert.equal(SURVEYS_MENU_ITEMS.length, 4);
    assert.equal(appSource.split("handleSidebarViewNavigation(e, 'surveys')").length - 1, 4);

    // Глава и сотрудник видят ровно один пункт, и стоит он под картой разделов.
    for (const user of [head(), employee()]) {
        const shown = menuItemsShown(user);
        assert.equal(shown.length, 1, user.role);
        assert.equal(shown[0].own, "departmentAllowsView(user, 'surveys')", user.role);
    }
    assert.equal(menuItemsShown(supervisor()).length, 0);
    assert.equal(menuItemsShown(employee('trainee')).length, 0);

    // Соседи — как были: глава отдела без «Опросов» в наборе, админ, тренер, линия.
    assert.equal(menuItemsShown({ id: 4, role: 'admin', department_code: 'hr', headed_department_id: 42 }).length, 0);
    assert.equal(menuItemsShown({ id: 7, role: 'admin' }).length, 1);
    assert.equal(menuItemsShown({ id: 8, role: 'trainer' }).length, 1);
    assert.equal(menuItemsShown({ id: 9, role: 'operator', department_code: 'szov' }).length, 1);
});

/* Где рисуется сам экран раздела: три места, каждое внутри своей ролевой ветки
   разметки. Ветка места — последняя открывающая строка верхнего уровня
   разметки (24 пробела) перед ним. Пункт меню без экрана — пустая страница. */
const SURVEYS_SCREENS = [...appSource.matchAll(/\{\(([^{}\n]+?) && \(<SurveysView user=\{user\}/g)].map((match) => {
    const openers = [...appSource.slice(0, match.index).matchAll(/\n {24}\{(?!\/\*)([^\n]+) && \(\n/g)];
    assert.ok(openers.length, 'ролевая ветка экрана «Опросы» не найдена — проверь тест');
    return { branch: openers[openers.length - 1][1], own: match[1] };
});
const HEAD_EMPLOYEE_VIEW = declarationOf('isDepartmentHeadAdminEmployeeView');

const screensShown = (user, view = 'surveys') => {
    const scope = { ...personFlags(user), view };
    scope.isDepartmentHeadAdminEmployeeView = evaluateIn(
        scope, `${HEAD_EMPLOYEE_VIEW}\nreturn isDepartmentHeadAdminEmployeeView;`,
    );
    return SURVEYS_SCREENS.filter((screen) => evaluateIn(
        scope, `return Boolean((${screen.branch}) && (${screen.own}));`,
    )).length;
};

test('экран «Опросы» рисуется главе и сотруднику — ровно в одной ветке разметки', () => {
    assert.equal(SURVEYS_SCREENS.length, 3);
    // Ветки — ролевые и разные; условие раздела стоит у самого экрана.
    assert.equal(new Set(SURVEYS_SCREENS.map((screen) => screen.branch)).size, 3);
    for (const screen of SURVEYS_SCREENS) assert.doesNotMatch(screen.branch, /\bview\b/, screen.branch);

    assert.equal(screensShown(head()), 1);
    assert.equal(screensShown(employee()), 1);
    assert.equal(screensShown({ id: 7, role: 'admin' }), 1);
    assert.equal(screensShown({ id: 8, role: 'trainer' }), 1);
    // В другом разделе экран опросов не рисуется никому.
    assert.equal(screensShown(head(), 'manage_users'), 0);
    assert.equal(screensShown(employee(), 'profile'), 0);
});

const BADGE_EFFECT = effectBody('/* Бейдж «Опросы» у рядового.', {
    openBefore: false, closing: '\n            }, [user?.id, currentUserRole, isScopedDepartmentHead, view]);',
});

test('бейдж непройденных опросов сотруднику фронт-офиса считается при входе и в разделе', () => {
    const asked = (user, view) => {
        let calls = 0;
        evaluateIn({ ...personFlags(user), view, fetchSurveysPendingBadgeCount: () => { calls += 1; } }, BADGE_EFFECT);
        return calls;
    };
    assert.equal(asked(employee(), 'profile'), 1);
    assert.equal(asked(employee(), 'surveys'), 1);
    // Главе число приносит общий эффект входа; этот — только рядовому.
    assert.equal(asked(head(), 'manage_users'), 0);
});

const { pickMobileTabs } = new Function(
    `${['MOBILE_TAB_SECTIONS', 'MOBILE_TAB_LIMIT', 'pickMobileTabs'].map(declarationOf).join('\n')}
     return { pickMobileTabs };`,
)();

// Набор флагов бара — тот самый объект, который App передаёт в pickMobileTabs.
const MOBILE_ACCESS = (() => {
    const memoAt = appSource.indexOf('const mobileTabItems = useMemo(() => pickMobileTabs(');
    assert.ok(memoAt >= 0, 'набор кнопок бара не найден — проверь тест');
    const open = appSource.indexOf('{', memoAt);
    let depth = 0;
    for (let i = open; i < appSource.length; i += 1) {
        if (appSource[i] === '{') depth += 1;
        else if (appSource[i] === '}') {
            depth -= 1;
            if (depth === 0) return appSource.slice(open, i + 1);
        }
    }
    throw new Error('не нашёл конец набора флагов бара — проверь тест');
})();

const mobileBar = (user, sections = {}) => {
    const access = evaluateIn({
        ...personFlags(user),
        wikiSectionEnabled: Boolean(sections.wiki),
        eventsSectionShown: departmentAllowsView(user, 'events'),
        canAccessGroupLateBotSection: Boolean(sections.groupLate),
    }, `return (${MOBILE_ACCESS});`);
    return { access, tabs: pickMobileTabs(access).map((tab) => tab.view) };
};

test('бар телефона у фронт-офисов прежний: «Опросы» открываются из шторки разделов', () => {
    // Сотрудник: в баре только личное. «Опросы» ему открыты, но раздел не личный.
    const staff = mobileBar(employee(), { wiki: true });
    assert.equal(staff.access.surveys, true);
    assert.deepEqual(staff.tabs, ['work_schedules']);
    // Стажёр: раздела нет ни в баре, ни во флагах.
    const trainee = mobileBar(employee('trainee'), { wiki: true });
    assert.equal(trainee.access.surveys, false);
    assert.deepEqual(trainee.tabs, ['work_schedules']);
    // Глава: четыре места бара заняты разделами, стоящими в реестре раньше.
    const manager = mobileBar(head(), { wiki: true, groupLate: true });
    assert.deepEqual(manager.tabs, ['qr_access', 'wiki', 'tasks', 'events']);
});
