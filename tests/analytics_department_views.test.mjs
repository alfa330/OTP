import assert from 'node:assert/strict';
import test from 'node:test';

import {
    departmentCodeHasOptionalEmployeeDirection,
    departmentCodeHidesEmployeeDirection,
    departmentCodeHidesEmployeeSipInput,
    departmentCodeHidesOperatorFields,
    departmentRestrictsViews,
} from '../src/utils/departmentViews.js';

/* Отдел аналитики (решение владельца 06.10.2026): при заведении сотрудника
   направление выбирать необязательно — у отдела их нет ни одного, а форма и
   сервер требовали выбрать. Проверяем поведение предиката, а не текст файла:
   набор из литералов, и опечатка в коде отдела синтаксис не ломает — она молча
   возвращает обязательность. */

const CODE = 'analytik';
const OTHER_CODES = [
    'szov', 'op', 'tez', 'front_office', 'remote_cc', 'accounting', 'hr', 'marketing', 'it',
    'request_processing_department',
];

test('аналитика: направление необязательно', () => {
    for (const code of [CODE, 'Analytik', ` ${CODE} `, 'ANALYTIK']) {
        assert.equal(departmentCodeHasOptionalEmployeeDirection(code), true, code);
    }
});

test('аналитика: поля «Направление» и «Группа» в карточке остаются', () => {
    // Необязательное — не скрытое: у ООЗ поля нет вовсе, здесь оно на месте.
    assert.equal(departmentCodeHidesEmployeeDirection(CODE), false);
    assert.equal(departmentCodeHidesOperatorFields(CODE), false);
    assert.equal(departmentCodeHidesEmployeeSipInput(CODE), false);
    // Разделов отделу никто не ограничивал — правка их не трогает.
    assert.equal(departmentRestrictsViews({ id: 5, role: 'operator', department_code: CODE }), false);
});

test('остальным отделам направление по-прежнему обязательно', () => {
    for (const code of OTHER_CODES) {
        assert.equal(departmentCodeHasOptionalEmployeeDirection(code), false, code);
    }
    // Похожие коды и «отдел не выбран» обязательность не снимают.
    for (const code of ['analytics', 'analytik2', 'analyt', 'аналитика', '', null, undefined, 2134]) {
        assert.equal(departmentCodeHasOptionalEmployeeDirection(code), false, String(code));
    }
});

test('ООЗ и аналитика — разные правила: скрытое не значит необязательное', () => {
    assert.equal(departmentCodeHidesEmployeeDirection('request_processing_department'), true);
    assert.equal(departmentCodeHasOptionalEmployeeDirection('request_processing_department'), false);
});
