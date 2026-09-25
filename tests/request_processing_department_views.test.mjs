import assert from 'node:assert/strict';
import test from 'node:test';

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
