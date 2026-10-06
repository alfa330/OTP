// Личный набор разделов: человеку портал показывает только перечисленное.
//
// Решение владельца 06.10.2026 про сотрудника отдела аналитики: «чтобы у этого
// сотрудника отображался только раздел байга». Набор строже карты отдела —
// в нём нет ни общих «Ивентов», ни «Библиотеки» по роли, ни «Вики» по отделу.
//
// Проверяем поведение предикатов, а не текст файла: карта из литералов, и
// опечатка в id синтаксис не ломает — она молча снимает ограничение целиком
// (человек снова видит меню оператора) или вешает его на чужую учётку.
//
// Запуск: node --test tests/personal_view_allowlist.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    BACK_OFFICE_EMPLOYEE_ROLES,
    departmentAllowsView,
    departmentRestrictsViews,
    firstAllowedView,
    personalViewsAllow,
    personalViewsOf,
} from '../src/utils/departmentViews.js';
import { isDepartmentHead, normalizeRole } from '../src/utils/roles.js';

const readLf = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const appSource = readLf('src/App.jsx');

/* Объявление `const NAME ...;` из App.jsx целиком — тем же приёмом, что
   mobile_tab_bar.test.mjs: App.jsx монолитен и не импортируется, а переписать
   реестр в тест значило бы проверять копию вместо кода. */
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


// Только id: ФИО в публичный репозиторий не кладём.
const ANALYST_ID = 540;

// Сотрудник отдела аналитики, как он заведён на проде: оператор без направления.
const analyst = (overrides = {}) => ({
    id: ANALYST_ID, role: 'operator', department_code: 'analytik', department_id: 2134, ...overrides,
});

// Всё, что портал показал бы оператору отдела без карты разделов, и общее.
const OTHER_VIEWS = [
    'events', 'wiki', 'library', 'profile', 'hours', 'work_schedules', 'shift_auction',
    'evaluation', 'ai_feedback', 'surveys', 'salary', 'contests', 'tasks', 'lms',
    'call_evaluation', 'call_division', 'manage_operators', 'qr_access', 'driver_mailings',
];

test('личный набор: человеку остаётся только «Списки Байги»', () => {
    const user = analyst();
    assert.deepEqual(personalViewsOf(user), ['baiga']);
    assert.equal(departmentRestrictsViews(user), true);
    assert.equal(departmentAllowsView(user, 'baiga'), true);
    assert.equal(personalViewsAllow(user, 'baiga'), true);
    for (const viewKey of OTHER_VIEWS) {
        assert.equal(departmentAllowsView(user, viewKey), false, viewKey);
        assert.equal(personalViewsAllow(user, viewKey), false, viewKey);
    }
    // Раздел по умолчанию: вход и гард видимости ведут человека сюда.
    assert.equal(firstAllowedView(user, []), 'baiga');
    assert.equal(firstAllowedView(user, ['hours', 'profile']), 'baiga');
});

test('личный набор строже карты отдела: «Ивентов» в нём тоже нет', () => {
    // У ООЗ карта оставляет общие «Ивенты» — личный набор их не оставляет.
    const ooz = { id: 3, role: 'operator', department_code: 'request_processing_department' };
    assert.equal(departmentAllowsView(ooz, 'events'), true);
    assert.equal(departmentAllowsView(analyst(), 'events'), false);
    // Набор заменяет карту отдела, а не пересекается с ней: карта любого
    // отдела, куда бы человека ни перевели, на него не действует.
    for (const code of ['op', 'tez', 'marketing', 'request_processing_department', 'szov', null]) {
        const moved = analyst({ department_code: code });
        assert.deepEqual(personalViewsOf(moved), ['baiga'], String(code));
        assert.equal(departmentAllowsView(moved, 'baiga'), true, String(code));
        assert.equal(departmentAllowsView(moved, 'salary'), false, String(code));
        assert.equal(firstAllowedView(moved, []), 'baiga', String(code));
    }
});

const RANK_AND_FILE = ['operator', 'trainee', ...BACK_OFFICE_EMPLOYEE_ROLES];
const OTHER_ROLES = ['sv', 'supervisor', 'trainer', 'admin', 'super_admin'];

test('личный набор — для рядового сотрудника, какой бы ни была его должность', () => {
    assert.deepEqual([...RANK_AND_FILE].sort(),
        ['accounting_manager', 'hr_manager', 'marketing_manager', 'operator', 'trainee']);
    for (const role of RANK_AND_FILE) {
        const user = analyst({ role });
        assert.deepEqual(personalViewsOf(user), ['baiga'], role);
        assert.equal(departmentAllowsView(user, 'events'), false, role);
        assert.equal(firstAllowedView(user, []), 'baiga', role);
    }
    // Роль в профиле бывает в другом регистре и с пробелами.
    assert.deepEqual(personalViewsOf(analyst({ role: ' Operator ' })), ['baiga']);
    // Id в профиле бывает и строкой.
    assert.deepEqual(personalViewsOf(analyst({ id: String(ANALYST_ID) })), ['baiga']);
    assert.equal(departmentAllowsView(analyst({ id: String(ANALYST_ID) }), 'events'), false);
});

test('личный набор не действует на супервайзера, тренера, админа и главу отдела', () => {
    /* У этих ролей свои гарды и свои безусловные пункты меню: у главы «Учет
       сотрудников» стоит без условий, а тренерский гард уводит из любого
       раздела вне своего списка в «Опросы» — с набором поверх он и гард отдела
       гоняли бы раздел друг другу без остановки. Повысили человека — у него
       меню новой роли, а не один раздел. */
    for (const role of OTHER_ROLES) {
        const user = analyst({ role });
        assert.equal(personalViewsOf(user), null, role);
        assert.equal(departmentRestrictsViews(user), false, role);
        assert.equal(departmentAllowsView(user, 'events'), true, role);
        assert.equal(personalViewsAllow(user, 'wiki'), true, role);
        assert.equal(personalViewsAllow(user, 'library'), true, role);
    }
    // Глава отдела — при любой базовой роли, включая рядовую.
    for (const role of [...RANK_AND_FILE, ...OTHER_ROLES]) {
        const head = analyst({ role, headed_department_id: 2134 });
        assert.equal(personalViewsOf(head), null, role);
        assert.equal(departmentAllowsView(head, 'events'), true, role);
    }
    assert.equal(personalViewsOf(analyst({ role: undefined })), null);
    assert.equal(personalViewsOf(analyst({ role: '' })), null);
});

test('«рядовой» у набора — тот же, что у ветки рядового в меню', () => {
    /* Набор проверен ровно на этой ветке сайдбара; разойдись списки ролей —
       человек получил бы набор при меню другой роли или наоборот. */
    const declaration = declarationOf('RANK_AND_FILE_ROLES');
    const appRoles = new Function('BACK_OFFICE_EMPLOYEE_ROLES', `${declaration} return RANK_AND_FILE_ROLES;`)(
        BACK_OFFICE_EMPLOYEE_ROLES,
    );
    assert.deepEqual([...appRoles].sort(), [...RANK_AND_FILE].sort());
    for (const role of appRoles) assert.deepEqual(personalViewsOf(analyst({ role })), ['baiga'], role);
    assert.ok(appSource.includes('const isRankAndFileRole = (role) => RANK_AND_FILE_ROLES.includes(normalizeRole(role));'));
    // Ветка меню рядового — под тем же условием «не глава отдела».
    assert.ok(appSource.includes('{isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead && ('));
    assert.ok(appSource.includes('const scopedDepartmentId = isDepartmentHeadUser && !isSuperAdmin ? headedDepartmentId(user) : null;'));
});

test('личный набор не задел никого другого', () => {
    // Сосед по отделу аналитики и его глава — как были: без ограничений.
    const neighbour = analyst({ id: ANALYST_ID + 1 });
    const head = { id: 229, role: 'admin', department_code: 'analytik', headed_department_id: 2134 };
    for (const user of [neighbour, head]) {
        assert.equal(personalViewsOf(user), null);
        assert.equal(departmentRestrictsViews(user), false);
        assert.equal(departmentAllowsView(user, 'events'), true);
        assert.equal(departmentAllowsView(user, 'hours'), true);
        for (const viewKey of ['wiki', 'library', 'events', 'baiga']) {
            assert.equal(personalViewsAllow(user, viewKey), true, viewKey);
        }
    }
    // Id, которые не должны совпасть с ключом карты ни при каком приведении.
    for (const id of [null, undefined, '', 'abc', '540a', 540.5, -540, 5400, 54, NaN, {}, [], true]) {
        assert.equal(personalViewsOf(analyst({ id })), null, String(id));
    }
    assert.equal(personalViewsOf(null), null);
    assert.equal(personalViewsOf(undefined), null);
    assert.equal(personalViewsAllow(null, 'wiki'), true);
    // Карты отделов работают по-прежнему.
    const tez = { id: 7, role: 'operator', department_code: 'tez' };
    assert.equal(departmentAllowsView(tez, 'hours'), true);
    assert.equal(departmentAllowsView(tez, 'tasks'), false);
    assert.equal(departmentAllowsView(tez, 'events'), true);
    assert.equal(firstAllowedView(tez, []), 'profile');
});

const { pickMobileTabs } = new Function(
    `${['MOBILE_TAB_SECTIONS', 'MOBILE_TAB_LIMIT', 'pickMobileTabs'].map(declarationOf).join('\n')}
     return { pickMobileTabs };`,
)();

/* ДВА СТРАЖА РАЗДЕЛА — ИХ НАСТОЯЩИЙ КОД. Перебросами в App.jsx заняты два
   эффекта: страж ролей («Do not touch view…») и гард «Этап 10». Их тела берём из
   файла как есть и исполняем: незнакомый идентификатор (флаг доступа, которого
   профилю не дали) читается как «нет». Так проверяется поведение пары, а не её
   текст: куда человек попадает и останавливается ли переброс. */
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

/* Куда приходит человек из раздела start и сколько раз его перебросили.
   Один проход — оба эффекта над одним и тем же view, побеждает последний
   setView (так React и поступает с двумя обновлениями одного прохода);
   redirectToView в тот же раздел — не переход. */
const settle = (user, start, access = {}) => {
    const role = normalizeRole(user?.role);
    const head = isDepartmentHead(user);
    const flags = {
        isAdminLikeRole: (role === 'admin' && !head) || role === 'super_admin',
        isDepartmentHeadUser: head,
        isPlainTrainer: role === 'trainer' && !head,
        wikiSectionEnabled: Boolean(access.wiki) && personalViewsAllow(user, 'wiki'),
        canAccessLibrarySection: Boolean(access.library) && personalViewsAllow(user, 'library'),
        canAccessBaigaSection: Boolean(access.baiga),
        canAccessDriverMailings: Boolean(access.mailings),
        canAccessCrmSection: Boolean(access.crm),
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

const STARTS = ['hours', 'events', 'library', 'wiki', 'profile', 'surveys', 'salary', 'tasks', 'lms',
    'four_you', 'work_schedules', 'evaluation', 'contests', 'manage_operators', 'baiga'];

test('стражи: человек с набором из любого раздела приходит в «Списки Байги» и остаётся', () => {
    for (const wiki of [true, false]) {
        for (const start of STARTS) {
            const result = settle(analyst(), start, { baiga: true, wiki, library: true });
            assert.equal(result.settled, true, `${start}: переброс не остановился: ${result.trail.join(' → ')}`);
            assert.equal(result.view, 'baiga', `${start}: ${result.trail.join(' → ')}`);
            assert.ok(result.trail.length <= 3, `${start}: лишние перебросы: ${result.trail.join(' → ')}`);
        }
    }
    // Вход: общий для рядового — в «Мои часы», оттуда тем же проходом в свой раздел.
    assert.deepEqual(settle(analyst(), 'hours', { baiga: true }).trail, ['hours', 'baiga']);
});

test('стражи: набор назвал раздел, а допуска нет — переброс не зацикливается', () => {
    /* Выключатель пилота или строка, забытая в списке аналитиков. До защиты
       страж ролей уводил из «Списков Байги» в «Мои часы», гард «Этап 10» — обратно,
       без остановки и с запросами на каждом витке (на стенде ~5000 переходов за
       7 секунд). Человек остаётся на пустом экране своего раздела. */
    for (const start of STARTS) {
        const result = settle(analyst(), start, { baiga: false, wiki: true, library: true });
        assert.equal(result.settled, true, `${start}: зациклился: ${result.trail.join(' → ')}`);
        assert.equal(result.view, 'baiga', `${start}: ${result.trail.join(' → ')}`);
    }
});

test('стражи: у остальных перебросы прежние', () => {
    // Сосед по отделу — оператор без карты разделов: стоит, где стоял.
    const neighbour = analyst({ id: ANALYST_ID + 1 });
    for (const start of ['hours', 'events', 'profile', 'surveys', 'salary']) {
        assert.deepEqual(settle(neighbour, start, { wiki: true, library: true }).trail, [start], start);
    }
    for (const start of ['wiki', 'library']) {
        assert.deepEqual(settle(neighbour, start, { wiki: true, library: true }).trail, [start], start);
    }
    // В чужие «Списки Байги» его не пускает страж раздела — как и раньше.
    assert.deepEqual(settle(neighbour, 'baiga', {}).trail, ['baiga', 'hours']);
    // Стажёр СЗоВ (карты у отдела нет, раздел ему закрыт) — тоже в «Мои часы».
    const trainee = { id: 8, role: 'trainee', department_code: 'szov' };
    assert.deepEqual(settle(trainee, 'baiga', {}).trail, ['baiga', 'hours']);
    // Оператор ОП: раздел открыт — стоит в нём; закрыт — оба стража уводят его
    // одним проходом, и побеждает гард отдела: раздел по умолчанию у ОП — «Зарплата».
    const sales = { id: 9, role: 'operator', department_code: 'op' };
    assert.deepEqual(settle(sales, 'baiga', { baiga: true }).trail, ['baiga']);
    assert.deepEqual(settle(sales, 'baiga', {}).trail, ['baiga', 'salary']);
    assert.deepEqual(settle(sales, 'events', {}).trail, ['events']);
    // Сотрудник ООЗ: из «Моих часов» в «Рассылки» — с допуском и без него.
    const ooz = { id: 3, role: 'operator', department_code: 'request_processing_department' };
    assert.deepEqual(settle(ooz, 'hours', { mailings: true }).trail, ['hours', 'driver_mailings']);
    assert.deepEqual(settle(ooz, 'hours', {}).trail, ['hours', 'driver_mailings']);
    assert.deepEqual(settle(ooz, 'events', {}).trail, ['events']);
    // Тот же id в роли тренера: набора нет, тренерский гард ведёт в «Опросы» и
    // на этом останавливается — с набором он и гард отдела гоняли бы раздел по кругу.
    const trainer = analyst({ role: 'trainer' });
    assert.deepEqual(settle(trainer, 'baiga', {}).trail, ['baiga', 'surveys']);
    assert.deepEqual(settle(trainer, 'events', {}).trail, ['events']);
});

test('бар телефона: «Ивенты» не попадают в него тому, у кого их нет в меню', () => {
    // Ровно то, что App считает человеку с личным набором «Списки Байги»:
    // ни одного раздела из реестра бара у него нет.
    const access = {
        qrAccess: false, myHours: false, myShifts: false, myShiftAuction: false, myEvaluations: false,
        wiki: false, tasks: false, lms: false, events: false, workSchedules: false, surveys: false,
        groupLate: false,
    };
    assert.deepEqual(pickMobileTabs(access, { events: 5 }), []);
    // «Ивенты» — такой же раздел с гейтом, как остальные: открыт — стоит в баре.
    assert.deepEqual(pickMobileTabs({ ...access, events: true }).map((tab) => tab.view), ['events']);
    // Флаг, которого App не передал, раздел не открывает.
    const { events, ...withoutFlag } = access;
    assert.deepEqual(pickMobileTabs(withoutFlag), []);
});

test('бар телефона: флаг «Ивентов» — тот же, что у пункта меню', () => {
    const memoAt = appSource.indexOf('const mobileTabItems = useMemo(');
    assert.ok(memoAt >= 0, 'набор кнопок бара не найден — проверь тест');
    const depsAt = appSource.indexOf('), [', memoAt);
    const memo = appSource.slice(memoAt, depsAt);
    const deps = appSource.slice(depsAt, appSource.indexOf(']);', depsAt));
    assert.match(memo, /\n\s+events: eventsSectionShown,\n/);
    assert.match(deps, /\beventsSectionShown\b/, 'бар не пересчитается при смене пользователя');
    assert.ok(appSource.includes('{eventsSectionShown && renderEventsSidebarItemInner()}'));
});
