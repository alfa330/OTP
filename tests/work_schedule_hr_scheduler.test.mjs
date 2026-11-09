// Отдел кадров ведёт графики любого отдела — решение владельца 09.11.2026: «нужно
// дать права HR, чтобы они могли вести графики любого отдела».
//
// В разделе кадровик правит смены, выходные и статусы и выгружает график. Работа
// руководителя линии — штраф, тренинги, тех. сбои, офлайн-активности, перерывы,
// импорт, агрегация, синхронизация статусов, разбор заявок — ему не выдана: сервер
// ответит отказом, поэтому и кнопок у него нет. Тренеру (просмотр) ничего не
// меняется, глобальному админу из отдела кадров — тоже.
//
// Исполняются настоящие объявления из App.jsx; разметку меню сверяем по ближайшему
// условию над пунктом.
//
// Запуск: node --test tests/work_schedule_hr_scheduler.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { isAdminLikeRole, isDepartmentHead, normalizeRole } from '../src/utils/roles.js';
import { departmentAllowsView, managesEmployeeAccounting } from '../src/utils/departmentViews.js';

const appSource = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const declarationOf = (name) => {
    const at = appSource.indexOf(`const ${name} `);
    assert.ok(at >= 0, `объявление ${name} не найдено — проверь тест`);
    assert.equal(appSource.indexOf(`const ${name} `, at + 1), -1, `объявление ${name} не одно`);
    let depth = 0;
    for (let i = at; i < appSource.length; i += 1) {
        const ch = appSource[i];
        if (ch === '(' || ch === '[' || ch === '{') depth += 1;
        else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
        else if (ch === ';' && depth === 0) return appSource.slice(at, i + 1);
    }
    throw new Error(`не нашёл конец объявления ${name} — проверь тест`);
};

const viewerFlags = (user, { plannerReadOnly = false } = {}) => (
    // eslint-disable-next-line no-new-func
    new Function(
        'user', 'plannerReadOnly', 'managesEmployeeAccounting', 'normalizeRole', 'isAdminLikeRoleFn', 'isDepartmentHead',
        `${declarationOf('plannerViewerIsHrScheduler')}
         ${declarationOf('plannerCanManageLine')}
         ${declarationOf('plannerViewerCanRemoveBlacklist')}
         return { plannerViewerIsHrScheduler, plannerCanManageLine, plannerViewerCanRemoveBlacklist };`,
    )(user, plannerReadOnly, managesEmployeeAccounting, normalizeRole, isAdminLikeRole, isDepartmentHead)
);

const HR_EMPLOYEE = { id: 7, role: 'hr_manager', department_code: 'hr', department_id: 30 };
const HR_HEAD = { id: 8, role: 'admin', department_code: 'hr', department_id: 30, headed_department_id: 30 };

test('кадровик и глава отдела кадров ведут график, но не линию', () => {
    for (const user of [HR_EMPLOYEE, HR_HEAD]) {
        const flags = viewerFlags(user);
        assert.equal(flags.plannerViewerIsHrScheduler, true, user.role);
        assert.equal(flags.plannerCanManageLine, false, user.role);
    }
    // «Убрать из ЧС» — как в «Учете сотрудников»: главе отдела кадров (admin) да,
    // рядовому кадровику нет — сервер пускает только админа и главу.
    assert.equal(viewerFlags(HR_HEAD).plannerViewerCanRemoveBlacklist, true);
    assert.equal(viewerFlags(HR_EMPLOYEE).plannerViewerCanRemoveBlacklist, false);
});

test('остальным раздел не меняется', () => {
    const cases = [
        [{ id: 1, role: 'super_admin', department_code: 'hr' }, true],
        [{ id: 2, role: 'admin', department_code: 'hr' }, true],
        [{ id: 3, role: 'admin', department_code: 'op', headed_department_id: 5 }, true],
        [{ id: 4, role: 'sv', department_code: 'szov' }, true],
        [{ id: 5, role: 'trainer', department_code: 'szov' }, false],
    ];
    for (const [user, canManage] of cases) {
        const flags = viewerFlags(user, { plannerReadOnly: user.role === 'trainer' });
        assert.equal(flags.plannerViewerIsHrScheduler, false, user.role);
        assert.equal(flags.plannerCanManageLine, canManage, user.role);
    }
});

test('пункт «Графики работы» — у кадровика, а не у бухгалтера и не у оператора HR', () => {
    assert.equal(departmentAllowsView(HR_EMPLOYEE, 'work_schedules'), true);
    assert.equal(departmentAllowsView({ id: 9, role: 'accounting_manager', department_code: 'accounting' }, 'work_schedules'), false);
    assert.equal(departmentAllowsView({ id: 12, role: 'operator', department_code: 'hr' }, 'work_schedules'), false);
});

test('штраф и синхронизации кадровику закрыты, тренеру и отделу — как раньше', () => {
    // eslint-disable-next-line no-new-func
    const canFine = new Function('plannerModalInGrantedDepartment', 'plannerViewerIsHrScheduler',
        `${declarationOf('plannerModalCanFine')} return plannerModalCanFine;`);
    assert.equal(canFine(false, true), false);
    assert.equal(canFine(false, false), true);
    assert.equal(canFine(true, false), false);

    // eslint-disable-next-line no-new-func
    const syncHidden = new Function('plannerScopeDepartmentCode', 'plannerViewerIsHrScheduler',
        `${declarationOf('plannerSyncActionsHidden')} return plannerSyncActionsHidden;`);
    assert.equal(syncHidden('szov', true), true);
    assert.equal(syncHidden('szov', false), false);
    assert.equal(syncHidden('front_office', false), true);
});

test('кадровик стартует со «Всех отделов»', () => {
    // eslint-disable-next-line no-new-func
    const ownDept = new Function('useMemo', 'user', 'plannerViewerIsHrScheduler',
        `${declarationOf('plannerOwnDepartmentId')} return plannerOwnDepartmentId;`);
    const useMemo = (fn) => fn();
    assert.equal(ownDept(useMemo, HR_EMPLOYEE, true), '');
    assert.equal(ownDept(useMemo, { department_id: 4 }, false), '4');
    assert.equal(ownDept(useMemo, { department_id: 4, headed_department_id: 6 }, false), '6');
});

test('вкладка «Контроль» в окне дня кадровику не показывается, тренеру — да', () => {
    // eslint-disable-next-line no-new-func
    const control = new Function('plannerViewerIsHrScheduler', 'modalShowTabs', 'modalActiveTab',
        `${declarationOf('modalTabControl')} return modalTabControl;`);
    assert.equal(control(true, true, 'control'), false);
    assert.equal(control(true, false, 'shifts'), false);
    assert.equal(control(false, true, 'control'), true);
    assert.ok(appSource.includes("plannerViewerIsHrScheduler ? null : { key: 'control', label: 'Контроль', icon: 'fa-shield-alt' },"));
    assert.ok(appSource.includes("plannerViewerIsHrScheduler ? null : { value: 'control', label: 'Контроль' },"));
    assert.ok(appSource.includes("{modalActiveTab === 'control' && !plannerViewerIsHrScheduler ? ("));
});

test('окна тренинга и офлайн-активности кадровику не открываются', () => {
    for (const name of ['openPlannerOfflineActivityModal', 'openPlannerTrainingModalForCurrentDay']) {
        const body = declarationOf(name);
        const firstStatement = body.split('\n')[1].trim();
        assert.equal(firstStatement, 'if (!plannerCanManageLine) return;', name);
    }
    assert.ok(declarationOf('handleOfflineTimelineRightMouseDown').includes('if (!plannerCanManageLine) return;'));
});

/* Ближайшее условие `{… && (` над пунктом разметки: пункт меню стоит под ним. */
const gateAbove = (needle) => {
    const at = appSource.indexOf(needle);
    assert.ok(at >= 0, `не найдено: ${needle}`);
    assert.equal(appSource.indexOf(needle, at + 1), -1, `не одно: ${needle}`);
    const lines = appSource.slice(0, at).split('\n').reverse();
    const opener = lines.find((line) => /\{[^{}]*&& \($/.test(line.trim()));
    assert.ok(opener, `над «${needle}» нет условия`);
    return opener.trim();
};

test('пункты руководителя линии стоят под условием', () => {
    const lineOnly = [
        'title="Импортировать график из Excel (ФИО + даты)"',
        'title="Настроить автогенерацию перерывов по направлениям"',
        'title="Выгрузить соответствие статусов с графиком за выбранный период"',
        'title="Журнал запросов на замену за выбранный месяц"',
        'title="Ручной пересчет метрик за выбранный день для всех операторов"',
        'Офлайн-активность: зажмите правую кнопку и протяните по таймлайну, затем отпустите.',
        '<div className={PLANNER_MENU_GROUP}>Спецрежим статусов</div>',
    ];
    for (const needle of lineOnly) {
        assert.match(gateAbove(needle), /plannerCanManageLine/, needle);
    }
    assert.match(gateAbove('title="Заявки операторов"'), /!plannerViewerIsHrScheduler/);
    // Выгрузка остаётся: это и есть ведение графика.
    assert.doesNotMatch(
        gateAbove('title="Экспортировать график (видимый диапазон, формат День/Неделя/Месяц и активные фильтры) в Excel"'),
        /plannerCanManageLine|plannerViewerIsHrScheduler/,
    );
    // Телефонная версия раздела: те же решения.
    assert.ok(appSource.includes("plannerViewerIsHrScheduler ? null : {\n                        key: 'requests',"));
    assert.ok(appSource.includes("(wsIsDayMode && plannerCanManageLine) ? {\n                        key: 'aggregate',"));
    // Очередь заявок и отпусков кадровику не грузится вовсе.
    assert.ok(declarationOf('loadReviewRequests').includes('if (isOperatorSelfSchedules || plannerViewerIsHrScheduler || !user) return;'));
    assert.ok(appSource.includes('enabled: !isOperatorSelfSchedules && plannerCanManageLine && !!user,'));
});

test('в меню у кадровика пункт называется «Графики работы», в нижнем баре — «Графики»', () => {
    assert.ok(appSource.includes("<span className=\"sidebar-text\">{isEmployeeAccountingManager ? 'Графики работы' : 'Мои смены'}</span>"));
    assert.ok(appSource.includes(
        "myShifts: isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead\n"
        + '                        && !isEmployeeAccountingManager\n'
        + "                        && departmentAllowsView(user, 'work_schedules'),",
    ));
});
