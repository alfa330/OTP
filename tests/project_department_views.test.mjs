import assert from 'node:assert/strict';
import test from 'node:test';

import {
    DEPARTMENT_VIEW_ALLOWLIST,
    departmentAllowsView,
    departmentCodeHasOptionalEmployeeGroup,
    departmentCodeHidesEmployeeDirection,
    departmentCodeHidesEmployeeInternship,
    departmentCodeHidesEmployeeSip,
    departmentCodeHidesEmployeeSipInput,
    departmentCodeHidesEmployeeSupervisor,
    departmentCodeHidesEmployeeTaxiproId,
    departmentCodeHidesFrontOfficeTraining,
    departmentCodeHidesOperatorFields,
    departmentCodeUsesEmployeeJobTitle,
    departmentHidesColleagueSchedules,
    departmentRestrictsViews,
    departmentUsesSimpleEmployeeAccounting,
    employeeRoleForDepartmentCode,
    firstAllowedView,
} from '../src/utils/departmentViews.js';

/* Отделы-проекты и АХО — решение владельца 09.11.2026: «Тез и всё, что касается
   Тез, — отдельный проект, Eazy Coffee и Eazy Go тоже: создать проект, потом
   внутри них группы», разработчики — свой отдел, и «по всем нужно сделать
   возможность вести графики в Графиках работы».

   Проект в iCore — отдел, подразделения — группы. Устроены как ООЗ: человек —
   оператор в группе, направлений нет, ни SIP, ни супервайзеров, есть
   «Должность». АХО — как «Аренда авто»: ни групп, ни направлений.

   Проверяем поведение предикатов: опечатка в коде отдела синтаксис не ломает —
   она молча снимает ограничение целиком, и рядовой увидел бы меню колл-центра. */

const PROJECTS = ['tez_project', 'eazy_coffee', 'eazy_go', 'development'];
const AHO = 'aho';
const ALL = [...PROJECTS, AHO];

const employee = (code, role = 'operator') => ({ id: 41, role, department_code: code });
// Глава — оператор с назначением главой: ограничения головного набора на нём видны.
const head = (code) => ({ id: 40, role: 'operator', department_code: code, department_id: 990, headed_department_id: 990 });

for (const code of ALL) {
    for (const role of ['operator', 'trainee']) {
        test(`${code}: ${role} видит «Профиль» и свои смены`, () => {
            const user = employee(code, role);
            assert.equal(departmentRestrictsViews(user), true);
            assert.equal(departmentAllowsView(user, 'profile'), true);
            assert.equal(departmentAllowsView(user, 'work_schedules'), true);
            for (const denied of [
                'hours', 'evaluation', 'surveys', 'salary', 'tasks', 'contests', 'shift_auction',
                'call_evaluation', 'lms', 'manage_operators', 'groups', 'events', 'wiki',
            ]) {
                assert.equal(departmentAllowsView(user, denied), false, denied);
            }
            assert.equal(firstAllowedView(user, []), 'profile');
        });
    }

    test(`${code}: рядовой заводится оператором, а не бэк-офисной должностью`, () => {
        // Роль 'operator' нужна графику: «Мои смены» сервер отдаёт только оператору,
        // а бэк-офисная роль увела бы человека в чужие разделы.
        assert.equal(employeeRoleForDepartmentCode('operator', code), 'operator');
    });

    test(`${code}: в карточке нет полей колл-центра, есть «Должность»`, () => {
        assert.equal(departmentCodeUsesEmployeeJobTitle(code), true);
        assert.equal(departmentCodeHidesEmployeeSupervisor(code), true);
        assert.equal(departmentCodeHidesEmployeeSip(code), true);
        assert.equal(departmentCodeHidesFrontOfficeTraining(code), true);
        assert.equal(departmentHidesColleagueSchedules(employee(code)), true, 'смены коллег строятся по направлению');
        assert.equal(departmentUsesSimpleEmployeeAccounting(head(code)), true, 'без пунктов «Супервайзеры» и «Тренеры»');
    });
}

for (const code of PROJECTS) {
    test(`${code}: глава ведёт людей, группы, графики и задачи`, () => {
        assert.deepEqual(DEPARTMENT_VIEW_ALLOWLIST[code], { head: ['manage_operators', 'groups', 'work_schedules', 'tasks'] });
        const user = head(code);
        for (const allowed of ['manage_operators', 'groups', 'work_schedules', 'tasks']) {
            assert.equal(departmentAllowsView(user, allowed), true, allowed);
        }
        for (const denied of ['hours', 'evaluation', 'salary', 'call_evaluation', 'shift_auction']) {
            assert.equal(departmentAllowsView(user, denied), false, denied);
        }
    });

    test(`${code}: проект делится группами, направлений нет`, () => {
        // Группа в карточке остаётся и при заведении обязательна — как у ООЗ.
        assert.equal(departmentCodeHidesOperatorFields(code), false);
        assert.equal(departmentCodeHasOptionalEmployeeGroup(code), false);
        assert.equal(departmentCodeHidesEmployeeDirection(code), true);
        assert.equal(departmentCodeHidesEmployeeSipInput(code), true);
        assert.equal(departmentCodeHidesEmployeeInternship(code), true);
        assert.equal(departmentCodeHidesEmployeeTaxiproId(code), true);
    });
}

test('aho: без групп и направлений, глава ведёт людей, графики и задачи', () => {
    assert.equal(departmentCodeHidesOperatorFields(AHO), true);
    assert.deepEqual(DEPARTMENT_VIEW_ALLOWLIST[AHO], { head: ['manage_operators', 'work_schedules', 'tasks'] });
    assert.equal(departmentAllowsView(head(AHO), 'groups'), false);
});

test('регистр и пробелы в коде отдела не важны', () => {
    assert.equal(departmentCodeHidesEmployeeDirection(' TEZ_Project '), true);
    assert.equal(departmentCodeHidesOperatorFields(' AHO '), true);
});

test('у глав бэк-офиса и «Аренды авто» есть «Графики работы»', () => {
    for (const code of ['accounting', 'hr', 'car_rental']) {
        assert.equal(departmentAllowsView(head(code), 'work_schedules'), true, code);
    }
    // Рядовым бэк-офиса «Мои смены» не выдаются: ручка своих смен — только оператору.
    assert.equal(departmentAllowsView(employee('accounting', 'accounting_manager'), 'work_schedules'), false);
    // Рядовым «Аренды авто» — как решил владелец по #391: только «Профиль».
    assert.equal(departmentAllowsView(employee('car_rental'), 'work_schedules'), false);
});
