import assert from 'node:assert/strict';
import test from 'node:test';

import {
    departmentCodeHasOptionalEmployeeDirection,
    departmentCodeHasOptionalEmployeeGroup,
    departmentCodeHidesEmployeeDirection,
    departmentCodeHidesOperatorFields,
} from '../src/utils/departmentViews.js';

/* IT (решение владельца 09.10.2026): при заведении сотрудника не обязательны ни
   группа, ни направление. Проверяем поведение предикатов, а не текст файла:
   наборы из литералов, и опечатка в коде отдела синтаксис не ломает — она молча
   возвращает обязательность. */

const CODE = 'it';
const OTHER_CODES = [
    'szov', 'op', 'tez', 'front_office', 'remote_cc', 'accounting', 'hr', 'marketing',
    'request_processing_department', 'analytik',
];

test('IT: группа и направление необязательны', () => {
    for (const code of [CODE, 'IT', ` ${CODE} `, 'It']) {
        assert.equal(departmentCodeHasOptionalEmployeeGroup(code), true, code);
        assert.equal(departmentCodeHasOptionalEmployeeDirection(code), true, code);
    }
});

test('IT: поля «Группа» и «Направление» в карточке остаются', () => {
    // Необязательное — не скрытое: выбрать группу и направление по-прежнему можно.
    assert.equal(departmentCodeHidesOperatorFields(CODE), false);
    assert.equal(departmentCodeHidesEmployeeDirection(CODE), false);
});

test('остальным отделам группа по-прежнему обязательна', () => {
    for (const code of OTHER_CODES) {
        assert.equal(departmentCodeHasOptionalEmployeeGroup(code), false, code);
    }
    // Похожие коды и «отдел не выбран» обязательность не снимают.
    for (const code of ['its', 'it_department', 'i t', 'ит', '', null, undefined, 4]) {
        assert.equal(departmentCodeHasOptionalEmployeeGroup(code), false, String(code));
        assert.equal(departmentCodeHasOptionalEmployeeDirection(code), false, String(code));
    }
});
