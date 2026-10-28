import assert from 'node:assert/strict';
import test from 'node:test';

import {
    DEPARTMENT_VIEW_ALLOWLIST,
    departmentAllowsView,
    departmentCodeHidesEmployeeSip,
    departmentCodeHidesEmployeeSupervisor,
    departmentCodeHidesFrontOfficeTraining,
    departmentCodeHidesOperatorFields,
    departmentCodeUsesEmployeeJobTitle,
    departmentRestrictsViews,
    departmentUsesSimpleEmployeeAccounting,
    firstAllowedView,
} from '../src/utils/departmentViews.js';

/* Аренда авто (код car_rental, ТЗ #391). Отдел устроен как бэк-офис: ни линии,
   ни направлений, ни групп, ни SIP, ни супервайзеров. Рабочий раздел один —
   «Аренда авто», его открывает флаг rental_access, а не карта; рядовым карта
   оставляет только «Профиль», главе — «Учет сотрудников» и «Задачи». Проверяем
   поведение предикатов: опечатка в коде отдела синтаксис не ломает — она молча
   снимает ограничение целиком. */

const CODE = 'car_rental';
const employee = (role = 'operator') => ({ id: 31, role, department_code: CODE });
// Глава отдела — оператор с назначением главой: админом портала её не делаем.
const head = () => ({
    id: 30, role: 'operator', department_code: CODE, department_id: 900, headed_department_id: 900,
});

for (const role of ['operator', 'trainee']) {
    test(`аренда: ${role} видит только «Профиль»`, () => {
        const user = employee(role);
        assert.equal(departmentRestrictsViews(user), true);
        assert.equal(departmentAllowsView(user, 'profile'), true);
        for (const denied of [
            'hours', 'work_schedules', 'evaluation', 'surveys', 'salary', 'tasks', 'contests',
            'shift_auction', 'call_evaluation', 'lms', 'manage_operators', 'events', 'wiki',
        ]) {
            assert.equal(departmentAllowsView(user, denied), false, denied);
        }
        // После входа — в «Профиль»: firstAllowedView берёт первый раздел набора.
        assert.equal(firstAllowedView(user, []), 'profile');
    });
}

test('аренда: глава ведёт сотрудников и задачи', () => {
    const user = head();
    assert.deepEqual(DEPARTMENT_VIEW_ALLOWLIST[CODE], { head: ['manage_operators', 'tasks'] });
    assert.equal(departmentRestrictsViews(user), true);
    assert.equal(departmentAllowsView(user, 'manage_operators'), true);
    assert.equal(departmentAllowsView(user, 'tasks'), true);
    for (const denied of ['hours', 'work_schedules', 'evaluation', 'salary', 'call_evaluation']) {
        assert.equal(departmentAllowsView(user, denied), false, denied);
    }
    // Супервайзеров в отделе нет: «Учет сотрудников» — сразу список людей.
    assert.equal(departmentUsesSimpleEmployeeAccounting(user), true);
});

test('аренда: в карточке сотрудника нет полей линии, есть «Должность»', () => {
    assert.equal(departmentCodeHidesOperatorFields(CODE), true);
    assert.equal(departmentCodeHidesEmployeeSupervisor(CODE), true);
    assert.equal(departmentCodeHidesEmployeeSip(CODE), true);
    assert.equal(departmentCodeHidesFrontOfficeTraining(CODE), true);
    assert.equal(departmentCodeUsesEmployeeJobTitle(CODE), true);
    // Регистр и пробелы в коде отдела не важны — как у остальных отделов.
    assert.equal(departmentCodeHidesOperatorFields(' Car_Rental '), true);
});
