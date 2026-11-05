/*
 * Список «Учета сотрудников» на телефоне.
 *
 * Кого показывать и как складываются группы «Операторов», решает сервер
 * (employees/page.py, tests/test_employees_page_postgres.py) — здесь закреплены
 * правила раскладки, которых у таблицы нет: какие метки статусов лишние, что
 * попадает в карточку и когда статус нужен в строке.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildCardSections,
    isEmptyValue,
    phonePage,
    plural,
    rowStatusNote,
    sortOptionsFromColumns,
    visibleStatusChips,
} from '../src/components/employees/employeesPhoneList.js';
import { pageOfResponse } from '../src/components/employees/employeesPaging.js';

const TABS = [
    { key: 'active', label: 'Активные' },
    { key: 'working', label: 'Работает' },
    { key: 'bs', label: 'Б/С' },
    { key: 'sick_leave', label: 'Больничный' },
    { key: 'annual_leave', label: 'Отпуск' },
    { key: 'fired', label: 'Уволенные' },
];

// Числа на метках — как их отдаёт сервер (status_counts): «Активные» — все,
// кроме уволенных; unpaid_leave считается в Б/С, «Увольнение» — в «Уволенных».
const countsOf = (...statuses) => {
    const counts = Object.fromEntries(TABS.map((tab) => [tab.key, 0]));
    statuses.forEach((status) => {
        const key = status === 'unpaid_leave' ? 'bs' : status === 'dismissal' ? 'fired' : status;
        if (key !== 'fired') counts.active += 1;
        if (key in counts && key !== 'active') counts[key] += 1;
    });
    return counts;
};
const chipKeys = (statuses, active) => visibleStatusChips(TABS, countsOf(...statuses), active)
    .map((tab) => tab.key);
const people = (...statuses) => statuses;

test('русские формы числа', () => {
    const words = ['сотрудник', 'сотрудника', 'сотрудников'];
    assert.equal(plural(1, words), 'сотрудник');
    assert.equal(plural(3, words), 'сотрудника');
    assert.equal(plural(5, words), 'сотрудников');
    assert.equal(plural(11, words), 'сотрудников');
    assert.equal(plural(14, words), 'сотрудников');
    assert.equal(plural(21, words), 'сотрудник');
    assert.equal(plural(0, words), 'сотрудников');
});

test('все на работе — полосы статусов нет: выбирать не из чего', () => {
    assert.deepEqual(chipKeys(people('working', 'working'), 'active'), []);
});

test('«Работает» при равенстве с «Активными» и нулевые статусы не показываются', () => {
    assert.deepEqual(chipKeys(people('working', 'working', 'fired'), 'active'), ['active', 'fired']);
});

test('отсутствующие появляются вместе с «Работает», которое теперь отличается', () => {
    assert.deepEqual(
        chipKeys(people('working', 'sick_leave', 'unpaid_leave'), 'active'),
        ['active', 'working', 'bs', 'sick_leave'],
    );
});

test('выбранный статус остаётся, даже если за ним никого', () => {
    assert.deepEqual(chipKeys(people('working'), 'annual_leave'), ['active', 'annual_leave']);
});

test('пустое значение — это пусто, прочерк и false, но не «Нет» и не ноль', () => {
    assert.equal(isEmptyValue(''), true);
    assert.equal(isEmptyValue(' - '), true);
    assert.equal(isEmptyValue(null), true);
    assert.equal(isEmptyValue(false), true);
    assert.equal(isEmptyValue('Нет'), false);
    assert.equal(isEmptyValue(0), false);
});

test('карточка: без имени и статуса, без повторов, без пустых полей и групп', () => {
    const columns = {
        general: [
            { key: 'name', label: 'Имя' },
            { key: 'status', label: 'Статус' },
            { key: 'hire_date', label: 'Дата найма' },
            { key: 'rate', label: 'Ставка' },
        ],
        contacts: [
            { key: 'name', label: 'Имя' },
            { key: 'email', label: 'Почта' },
            { key: 'instagram', label: 'Инстаграм' },
        ],
        corporate: [
            { key: 'name', label: 'Имя' },
            { key: 'hire_date', label: 'Дата найма' },
            { key: 'internship_in_company', label: 'Практика' },
        ],
    };
    const values = { hire_date: '20.07.2025', rate: '1.00', email: '-', instagram: '', internship_in_company: 'Нет' };
    const sections = buildCardSections(
        [
            { key: 'general', label: 'Общее' },
            { key: 'contacts', label: 'Контакты' },
            { key: 'corporate', label: 'Корпоративное' },
        ],
        (key) => columns[key],
        (column) => values[column.key],
    );
    assert.deepEqual(
        sections.map((section) => [section.title, section.fields.map((field) => field.label)]),
        [
            ['Общее', ['Дата найма', 'Ставка']],
            ['Корпоративное', ['Практика']],
        ],
    );
});

test('статус в строке: норма и выбранная вкладка молчат, отсутствие и увольнение говорят', () => {
    const note = (code, statusTab, label, blacklist = false) => rowStatusNote({ code, statusTab, label, blacklist });
    assert.equal(note('working', 'active', 'Работает'), null);
    assert.equal(note('sick_leave', 'sick_leave', 'Больничный'), null);
    assert.equal(note('fired', 'fired', 'Уволен'), null);
    assert.deepEqual(note('bs', 'active', 'Б/С'), { text: 'Б/С', tone: 'warn' });
    assert.deepEqual(note('dismissal', 'fired', 'Увольнение'), { text: 'Увольнение', tone: 'danger' });
    assert.deepEqual(note('fired', 'fired', 'Уволен', true), { text: 'ЧС', tone: 'danger' });
    assert.deepEqual(note('dismissal', 'fired', 'Увольнение', true), { text: 'Увольнение · ЧС', tone: 'danger' });
});

test('порядок предлагается только по сортируемым колонкам списка', () => {
    const options = sortOptionsFromColumns([
        { key: 'name', sortField: 'name' },
        { key: 'has_proxy' },
        { key: 'hire_date', sortField: 'hire_date' },
        { key: 'mystery', sortField: 'mystery' },
    ]);
    assert.deepEqual(options.map((option) => [option.field, option.dir]), [['name', 'asc'], ['hire_date', 'desc']]);
});

// Страница с сервера: найдено 120 из 300 во всём списке, на экране вторая, 50 строк.
const SERVER_PAGE = pageOfResponse({
    employees: Array.from({ length: 50 }, (_, i) => ({ id: 51 + i, name: `Свежий ${51 + i}` })),
    total: 120, list_total: 300, offset: 50, limit: 50,
    status_counts: { active: 280, working: 270, fired: 20 },
});

test('телефон: итог, числа на метках и номер страницы — из ответа сервера', () => {
    const view = phonePage({ pageData: SERVER_PAGE, rows: [], selectedIds: new Set() });
    assert.equal(view.total, 120, 'найдено, а не весь список');
    assert.equal(view.listTotal, 300);
    assert.deepEqual(view.counts, { active: 280, working: 270, fired: 20 });
    assert.deepEqual([view.paged.page, view.paged.pageCount, view.paged.from, view.paged.to], [2, 3, 51, 100]);
    assert.deepEqual(view.paged.groups.map((group) => [group.key, group.rows.length, group.total]), [['all', 50, 120]]);
});

test('телефон: группы «Операторов» — шапки из ответа, число — вся группа', () => {
    const pageData = pageOfResponse({
        employees: [{ id: 1, list_group: 'mine' }, { id: 2, list_group: 'dir:Чат' }, { id: 3, list_group: 'dir:Чат' }],
        total: 40, offset: 0, limit: 50,
        groups: [{ key: 'mine', title: 'Мои операторы', total: 1 }, { key: 'dir:Чат', title: 'Чат', total: 39 }],
    });
    assert.deepEqual(phonePage({ pageData, rows: [] }).paged.groups.map((g) => [g.title, g.rows.length, g.total]),
        [['Мои операторы', 1, 1], ['Чат', 2, 39]]);
});

test('телефон: «все выбраны» — только когда выбраны все найденные, а не одна страница', () => {
    const page = (ids) => phonePage({ pageData: SERVER_PAGE, rows: [], selectedIds: new Set(ids) });
    const onPage = SERVER_PAGE.rows.map((row) => row.id);
    assert.equal(page(onPage).allSelected, false, '50 из 120');
    const all = Array.from({ length: 120 }, (_, i) => i + 1);
    assert.equal(page(all).allSelected, true);
    assert.equal(page(all.filter((id) => id !== 60)).allSelected, false, 'снят один со страницы');
    assert.equal(phonePage({ pageData: pageOfResponse({ employees: [], total: 0 }), rows: [], selectedIds: new Set() }).allSelected, false);
});

test('телефон: карточку ищем во всём списке, строка страницы свежее', () => {
    const view = phonePage({
        pageData: SERVER_PAGE,
        rows: [{ id: 1, name: 'Только в полном списке' }, { id: 60, name: 'Старое имя' }],
        selectedIds: new Set(),
    });
    assert.equal(view.rowsById.get(1)?.name, 'Только в полном списке');
    assert.equal(view.rowsById.get(60)?.name, 'Свежий 60');
});
