/*
 * Список «Учета сотрудников» на телефоне.
 *
 * Кого показывать решают функции таблицы (App.jsx) — здесь закреплены правила,
 * которых у таблицы нет: какие метки статусов лишние, как складываются группы
 * «Операторов», что попадает в карточку и когда статус нужен в строке.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildCardSections,
    countByStatus,
    filterEmployees,
    groupOperators,
    isEmptyValue,
    plural,
    rowStatusNote,
    sortOptionsFromColumns,
    visibleStatusChips,
} from '../src/components/employees/employeesPhoneList.js';

const TABS = [
    { key: 'active', label: 'Активные' },
    { key: 'working', label: 'Работает' },
    { key: 'bs', label: 'Б/С' },
    { key: 'sick_leave', label: 'Больничный' },
    { key: 'annual_leave', label: 'Отпуск' },
    { key: 'fired', label: 'Уволенные' },
];

// Та же лестница, что isEmployeeVisibleByStatusTab в App.jsx.
const filterKey = (status) => {
    const value = String(status || '').trim().toLowerCase() || 'working';
    if (value === 'unpaid_leave') return 'bs';
    if (value === 'dismissal') return 'fired';
    return value;
};
const isVisible = (status, tab) => {
    const key = filterKey(status);
    if (tab === 'active') return key !== 'fired';
    if (tab === 'fired') return key === 'fired';
    return key === tab;
};
const chipKeys = (rows, active) => visibleStatusChips(TABS, countByStatus(rows, TABS, isVisible), active)
    .map((tab) => tab.key);
const people = (...statuses) => statuses.map((status, i) => ({ id: i + 1, name: `Сотрудник ${i + 1}`, status }));

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

test('фильтр списка — статус и поиск вместе', () => {
    const rows = [
        { id: 1, name: 'Алиев Арман', status: 'working' },
        { id: 2, name: 'Бекова Дана', status: 'fired' },
        { id: 3, name: 'Алимова Сая', status: 'dismissal' },
    ];
    const matches = (row, q) => row.name.toLowerCase().includes(q.toLowerCase());
    const ids = (statusTab, query) => filterEmployees(rows, { statusTab, isVisible, query, matches }).map((r) => r.id);
    assert.deepEqual(ids('active', ''), [1]);
    assert.deepEqual(ids('fired', ''), [2, 3]);
    assert.deepEqual(ids('fired', 'али'), [3]);
    assert.deepEqual(ids('active', '  '), [1]);
});

const OPERATORS = [
    { id: 1, name: 'Вера', direction: 'Чат', mine: true },
    { id: 2, name: 'Анна', direction: 'Линия', mine: false },
    { id: 3, name: 'Борис', direction: 'Чат', mine: false },
    { id: 4, name: 'Галина', direction: 'Линия', mine: false },
    { id: 5, name: 'Алия', direction: 'Линия', mine: true },
];
const byName = (a, b) => a.name.localeCompare(b.name, 'ru');
const grouped = (sortField, sortDir) => groupOperators(OPERATORS, {
    isMine: (row) => row.mine,
    directionOf: (row) => row.direction,
    compare: byName,
    sortField,
    sortDir,
}).map((group) => [group.title, group.rows.map((row) => row.name)]);

test('свои операторы первыми, дальше направления по алфавиту', () => {
    assert.deepEqual(grouped('name', 'asc'), [
        ['Мои операторы', ['Алия', 'Вера']],
        ['Линия', ['Анна', 'Галина']],
        ['Чат', ['Борис']],
    ]);
});

test('порядок «по направлению» от Я переставляет группы и имена внутри', () => {
    assert.deepEqual(grouped('direction', 'desc'), [
        ['Мои операторы', ['Вера', 'Алия']],
        ['Чат', ['Борис']],
        ['Линия', ['Галина', 'Анна']],
    ]);
});

test('без своих операторов группы «Мои операторы» нет', () => {
    const groups = groupOperators(OPERATORS.filter((row) => !row.mine), {
        isMine: (row) => row.mine, directionOf: (row) => row.direction, compare: byName, sortField: 'name', sortDir: 'asc',
    });
    assert.deepEqual(groups.map((group) => group.key), ['dir:Линия', 'dir:Чат']);
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
