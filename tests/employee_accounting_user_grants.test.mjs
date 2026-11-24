import assert from 'node:assert/strict';
import test from 'node:test';

import {
    EMPLOYEE_ACCOUNTING_LISTS_SCOPE,
    departmentsOutsideEmployeeAccounting,
    hasEmployeeAccountingUserGrant,
    hasFullEmployeeAccounting,
    keepOutsideEmployeeAccounting,
    managesEmployeeAccounting,
    outsideEmployeeAccountingDepartmentIds,
} from '../src/utils/departmentViews.js';

/* «Учет сотрудников» целиком — поимённо (решение владельца 24.11.2026): двум
   главам отделов, бухгалтерии и отдела аналитики. Допуск держится за отдел, в
   котором человек числится, и за id; графики любого отдела и «Кадры» остаются
   у отдела кадров (managesEmployeeAccounting). Совпадение набора с сервером —
   tests/test_employee_accounting_user_grants.py. */

const person = (id, departmentCode, extra = {}) => ({
    id, role: 'admin', department_code: departmentCode, ...extra,
});

test('каждому из набора — в его отделе — раздел целиком', () => {
    for (const [id, code] of [[467, 'accounting'], [229, 'analytik'], ['467', 'Accounting']]) {
        const user = person(id, code, { headed_department_id: 7 });
        assert.equal(hasEmployeeAccountingUserGrant(user), true, `${id} ${code}`);
        assert.equal(hasFullEmployeeAccounting(user), true, `${id} ${code}`);
        // Графики любого отдела — не им: это отдел кадров.
        assert.equal(managesEmployeeAccounting(user), false, `${id} ${code}`);
    }
});

test('перевод в другой отдел снимает допуск', () => {
    for (const code of ['hr', 'szov', 'analytik', null]) {
        assert.equal(hasEmployeeAccountingUserGrant(person(467, code)), false, String(code));
    }
    assert.equal(hasEmployeeAccountingUserGrant(person(229, 'accounting')), false);
});

test('остальным бухгалтерии и аналитики допуск не выдан', () => {
    for (const [id, code] of [[499, 'accounting'], [500, 'accounting'], [1, 'analytik']]) {
        assert.equal(hasEmployeeAccountingUserGrant(person(id, code)), false, `${id}`);
        assert.equal(hasFullEmployeeAccounting(person(id, code)), false, `${id}`);
    }
    assert.equal(hasEmployeeAccountingUserGrant(null), false);
    assert.equal(hasEmployeeAccountingUserGrant({ department_code: 'accounting' }), false);
});

test('отдел кадров — раздел целиком, но не поимённый допуск', () => {
    const hr = person(456, 'hr', { role: 'hr_manager' });
    assert.equal(hasFullEmployeeAccounting(hr), true);
    assert.equal(hasEmployeeAccountingUserGrant(hr), false);
    assert.equal(managesEmployeeAccounting(hr), true);
});

test('пометка раздела — та же строка, что ждёт сервер', () => {
    assert.equal(EMPLOYEE_ACCOUNTING_LISTS_SCOPE, 'employee_accounting');
});

/* Срезы для разделов ВНЕ «Учета сотрудников»: ровно то, что сервер отдал бы главе
   без ?scope (_headed_department_ids). Формы — как в ответах ручек: люди и СВ —
   department_id, направления — department_id и departmentId. */
const accountingHead = person(467, 'accounting', {
    headed_department_id: 1500,
    headed_departments: [{ id: 1500, name: 'Бухгалтерия', code: 'accounting' }],
});
const users = [
    { id: 1, role: 'operator', department_id: 1 },
    { id: 468, role: 'accounting_manager', department_id: 1500 },
    { id: 9, role: 'operator', department_id: null },
];
const svList = [{ id: 2, department_id: 1 }, { id: 3, department_id: 1500 }];
const directions = [{ id: 21, department_id: 1, departmentId: 1 }, { id: 30, departmentId: 1500 }];
const departments = [{ id: 1, code: 'szov' }, { id: 1500, code: 'accounting' }, { id: 2134, code: 'analytik' }];

test('глава с допуском: другим разделам — люди, СВ и направления её отдела', () => {
    const ids = outsideEmployeeAccountingDepartmentIds(accountingHead);
    assert.deepEqual([...ids], [1500]);
    assert.deepEqual(keepOutsideEmployeeAccounting(users, ids).map((u) => u.id), [468]);
    assert.deepEqual(keepOutsideEmployeeAccounting(svList, ids).map((u) => u.id), [3]);
    assert.deepEqual(keepOutsideEmployeeAccounting(directions, ids).map((d) => d.id), [30]);
    assert.deepEqual(departmentsOutsideEmployeeAccounting(departments, accountingHead).map((d) => d.id), [1500]);
});

test('глава нескольких отделов: срез людей — по всем её отделам, справочник — по первому', () => {
    const head = person(229, 'analytik', {
        headed_department_id: 2134,
        headed_departments: [{ id: 2134, name: 'Аналитика', code: 'analytik' }, { id: 1, name: 'СЗоВ', code: 'szov' }],
    });
    const ids = outsideEmployeeAccountingDepartmentIds(head);
    assert.deepEqual([...ids].sort((a, b) => a - b), [1, 2134]);
    assert.deepEqual(keepOutsideEmployeeAccounting(users, ids).map((u) => u.id), [1]);
    assert.deepEqual(departmentsOutsideEmployeeAccounting(departments, head).map((d) => d.id), [2134]);
});

test('без допуска и не главе — тот же массив, без среза', () => {
    for (const someone of [
        person(119, 'op', { headed_department_id: 191 }),
        person(456, 'hr', { role: 'hr_manager' }),
        person(467, 'accounting'), // допуск есть, но не глава — по роли admin он глобальный админ
        person(467, 'accounting', { role: 'super_admin', headed_department_id: 1500 }),
    ]) {
        assert.equal(outsideEmployeeAccountingDepartmentIds(someone), null);
        assert.equal(keepOutsideEmployeeAccounting(users, outsideEmployeeAccountingDepartmentIds(someone)), users);
        assert.equal(departmentsOutsideEmployeeAccounting(departments, someone), departments);
    }
});
