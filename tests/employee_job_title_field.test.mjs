// Поле «Должность» в карточке сотрудника — у каких отделов оно есть.
//
// Решение владельца 16.11.2026: должности по его «Списку работников» записаны
// всем, а у СЗоВ, ОП, IT и финаналитики поля в карточке не было — руководителю,
// тренеру, сисадмину и аналитику должность было не поправить. Поле добавлено
// только в ФОРМУ: списки этих отделов по-прежнему подписывают людей направлением.
//
// Запуск: node --test tests/employee_job_title_field.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    departmentCodeAsksEmployeeJobTitle,
    departmentCodeEmployeeJobTitlePlaceholder,
    departmentCodeUsesEmployeeJobTitle,
} from '../src/utils/departmentViews.js';

const FORM_ONLY = ['front_office', 'szov', 'op', 'it', 'analytik'];
const WITH_LISTS = ['accounting', 'hr', 'marketing', 'request_processing_department', 'car_rental',
    'tez_project', 'eazy_coffee', 'eazy_go', 'development', 'aho'];

test('форма спрашивает должность у отделов владельца и у прежних', () => {
    for (const code of [...FORM_ONLY, ...WITH_LISTS]) {
        assert.equal(departmentCodeAsksEmployeeJobTitle(code), true, code);
        assert.equal(departmentCodeAsksEmployeeJobTitle(` ${code.toUpperCase()} `), true, `${code}: регистр и пробелы`);
    }
});

test('отделам, которых владелец не называл, поле не добавлено', () => {
    for (const code of ['tez', 'remote_cc', 'unknown', '', null, undefined]) {
        assert.equal(departmentCodeAsksEmployeeJobTitle(code), false, String(code));
    }
});

test('списки этих отделов остаются при направлении', () => {
    // Набор «должность вместо направления» (подпись строки, колонка, дни рождения)
    // — отдельный: туда новые отделы не вписаны.
    for (const code of FORM_ONLY) {
        assert.equal(departmentCodeUsesEmployeeJobTitle(code), false, code);
    }
    for (const code of WITH_LISTS) {
        assert.equal(departmentCodeUsesEmployeeJobTitle(code), true, code);
    }
});

test('пример в пустом поле — из жизни отдела', () => {
    assert.equal(departmentCodeEmployeeJobTitlePlaceholder('front_office'), 'Например, менеджер');
    assert.equal(departmentCodeEmployeeJobTitlePlaceholder('szov'), 'Например, тренер');
    assert.equal(departmentCodeEmployeeJobTitlePlaceholder('op'), 'Например, руководитель ОП');
    assert.equal(departmentCodeEmployeeJobTitlePlaceholder('IT'), 'Например, системный администратор');
    assert.equal(departmentCodeEmployeeJobTitlePlaceholder('analytik'), 'Например, финансовый аналитик');
    for (const code of ['accounting', 'hr', 'eazy_go', null]) {
        assert.equal(departmentCodeEmployeeJobTitlePlaceholder(code), 'Например, бухгалтер по расчётам', String(code));
    }
    // У каждого отдела «только формы» пример свой: бухгалтерский в карточке
    // тренера или сисадмина читался бы как ошибка формы.
    for (const code of FORM_ONLY) {
        assert.notEqual(departmentCodeEmployeeJobTitlePlaceholder(code), 'Например, бухгалтер по расчётам', code);
    }
});

test('окно карточки берёт и показ поля, и пример из этих правил', () => {
    const modal = readFileSync(new URL('../src/components/modals/UserEditModal.jsx', import.meta.url), 'utf8')
        .split('\r\n').join('\n');
    assert.ok(modal.includes('const showEmployeeJobTitle = departmentCodeAsksEmployeeJobTitle(effectiveDeptCode);'));
    assert.ok(modal.includes('const employeeJobTitlePlaceholder = departmentCodeEmployeeJobTitlePlaceholder(effectiveDeptCode);'));
    // Поле — в обоих режимах окна: при создании учётки и при правке.
    assert.equal(modal.split('{showEmployeeJobTitle && (').length - 1, 2);
    assert.equal(modal.split('placeholder={employeeJobTitlePlaceholder}').length - 1, 2);
});
