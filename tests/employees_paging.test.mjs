/*
 * «Учет сотрудников» — списки по страницам.
 *
 * Арифметика страниц — в employeesPaging.js, общая у компьютера и телефона.
 * Проводка (какой список режется, где пейджер, когда страница сбрасывается)
 * живёт в App.jsx и EmployeesMobileView.jsx — она ломается молча при правке
 * разметки, поэтому сторожится здесь текстом.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
    EMPLOYEES_PAGE_SIZE,
    pageOfEmployeeGroups,
    pageOfEmployees,
} from '../src/components/employees/employeesPaging.js';

const here = dirname(fileURLToPath(import.meta.url));
// Git for Windows кладёт файлы в CRLF — сверяем с LF, как соседние тесты.
const read = (...parts) => readFileSync(join(here, '..', ...parts), 'utf8').split('\r\n').join('\n');
const APP = read('src', 'App.jsx');
const PHONE = read('src', 'components', 'employees', 'EmployeesMobileView.jsx');

const people = (count, prefix = 'p') => Array.from({ length: count }, (_, i) => ({ id: `${prefix}${i + 1}` }));
const ids = (rows) => rows.map((row) => row.id);

const between = (source, start, end) => {
    const from = source.indexOf(start);
    assert.ok(from >= 0, `нет начала «${start}» — разметка изменилась, проверь тест`);
    const to = source.indexOf(end, from);
    assert.ok(to > from, `нет конца «${end}» — разметка изменилась, проверь тест`);
    return source.slice(from, to);
};

const directorySource = () => between(APP, 'const renderEmployeeDirectorySection = ({', 'const changeSvTable = async');
const employeesSource = () => between(
    APP,
    "{(view === 'manage_users' || view === 'employees') && (\n",
    "{view === 'manage_admins' && (isSuperAdmin || isEmployeeAccountingManager) && renderEmployeeDirectorySection({",
);
const operatorsSource = () => between(APP, "{view === 'manage_operators' && !isMobileShell && (", "{view === 'operators' && (");

test('страница — 50 строк, как в «Сессиях» и «Касаниях»', () => {
    assert.equal(EMPLOYEES_PAGE_SIZE, 50);
    for (const total of [0, 1, 49, 50]) {
        assert.equal(pageOfEmployees(people(total), 1).pageCount, 1, `${total} человек — одна страница`);
    }
    assert.equal(pageOfEmployees(people(51), 1).pageCount, 2);
    assert.equal(pageOfEmployees(people(312), 1).pageCount, 7);
});

test('страницы не пересекаются, покрывают всех и сохраняют порядок', () => {
    const rows = people(123);
    const seen = [];
    const { pageCount } = pageOfEmployees(rows, 1);
    for (let page = 1; page <= pageCount; page += 1) {
        const slice = pageOfEmployees(rows, page);
        assert.equal(slice.from, (page - 1) * 50 + 1);
        assert.equal(slice.to, slice.from + slice.rows.length - 1);
        seen.push(...ids(slice.rows));
    }
    assert.deepEqual(seen, ids(rows));
    const last = pageOfEmployees(rows, 3);
    assert.deepEqual([last.from, last.to, last.rows.length, last.total], [101, 123, 23, 123]);
});

test('номер за краем прижимается: после увольнения последняя страница не пустая', () => {
    // Стояли на 7-й из 7, уволили одного — людей стало 300, страниц 6.
    const after = pageOfEmployees(people(300), 7);
    assert.equal(after.page, 6);
    assert.equal(after.rows.length, 50);
    for (const page of [0, -3, NaN, undefined, null, 'abc']) {
        assert.equal(pageOfEmployees(people(120), page).page, 1, `страница ${String(page)}`);
    }
    assert.equal(pageOfEmployees(people(120), '2.7').page, 2);
    const empty = pageOfEmployees([], 4);
    assert.deepEqual([empty.page, empty.pageCount, empty.total, empty.from, empty.to], [1, 1, 0, 0, 0]);
    assert.deepEqual(pageOfEmployees(null, 1).rows, []);
});

test('группы: шапка на каждой странице, где есть её люди, число — вся группа', () => {
    const groups = [
        { key: 'mine', title: 'Мои операторы', rows: people(30, 'm') },
        { key: 'dir:Линия', title: 'Линия', rows: people(45, 'l') },
        { key: 'dir:Пусто', title: 'Пусто', rows: [] },
        { key: 'dir:Чаты', title: 'Чаты', rows: people(10, 'c') },
    ];
    const first = pageOfEmployeeGroups(groups, 1);
    assert.equal(first.pageCount, 2);
    assert.equal(first.total, 85);
    assert.deepEqual(first.groups.map((g) => [g.key, g.rows.length, g.total]), [
        ['mine', 30, 30],
        ['dir:Линия', 20, 45],
    ]);
    const second = pageOfEmployeeGroups(groups, 2);
    assert.deepEqual(second.groups.map((g) => [g.key, g.title, g.rows.length, g.total]), [
        ['dir:Линия', 'Линия', 25, 45],
        ['dir:Чаты', 'Чаты', 10, 10],
    ]);
    assert.deepEqual([second.from, second.to], [51, 85]);
    // Порядок строк — тот же, что без страниц.
    const all = [...first.groups, ...second.groups].flatMap((g) => ids(g.rows));
    assert.deepEqual(all, groups.flatMap((g) => ids(g.rows)));
    // Пустая группа не рисует одинокую шапку.
    assert.ok(![...first.groups, ...second.groups].some((g) => g.key === 'dir:Пусто'));
    // Исходные группы не тронуты: по ним считаются «Выбрать всех» и счёт.
    assert.equal(groups[1].rows.length, 45);
});

test('компьютер: каждый из пяти списков режется на страницы и листается одним пейджером', () => {
    const directory = directorySource();
    assert.match(directory, /const paged = pageOfEmployees\(sortedRows, employeeListPage\);/);
    assert.match(directory, /\{paged\.rows\.map\(\(employee\) => \(/);
    assert.doesNotMatch(directory, /\{sortedRows\.map\(/, 'в таблицу снова пошёл весь список');

    const employees = employeesSource();
    assert.match(employees, /const paged = pageOfEmployees\(sortedUsersFlat, employeeListPage\);/);
    assert.match(employees, /const grouped = \{ __all__: paged\.rows \};/);

    const operators = operatorsSource();
    assert.match(operators, /const paged = pageOfEmployeeGroups\(\[/);
    assert.match(operators, /\{pagedMine\.rows\.map\(\(op\) => \(/);
    assert.match(operators, /\{pagedDirections\.map\(\(group\) => \(/);
    assert.doesNotMatch(operators, /\{myOperatorsSorted\.map\(|\{sortedDirectionNames\.map\(/, 'в таблицу снова пошёл весь список');

    for (const [name, source] of [['directory', directory], ['employees', employees], ['operators', operators]]) {
        assert.equal(source.split('<IosPager').length - 1, 1, `${name}: ровно один пейджер`);
        assert.match(source, /onPage=\{goEmployeeListPage\}/, name);
        assert.match(source, /\{paged\.pageCount > 1 && \(/, `${name}: на одной странице пейджера нет`);
        // Итог в подвале таблицы — только когда пейджера нет, иначе одно число дважды.
        assert.match(source, /\{paged\.pageCount === 1 && <>/, `${name}: счёт в подвале при пейджере`);
        assert.match(source, /<div ref=\{employeeListTopRef\} className="overflow-x-auto/, `${name}: якорь прокрутки`);
        // Пейджер — под таблицей, вне её поперечной прокрутки, и получает
        // прижатый номер: после увольнения последнего на странице подсвечен
        // тот номер, что на экране, а не исчезнувший.
        assert.ok(source.indexOf('<IosPager') > source.lastIndexOf('</table>'), `${name}: пейджер под таблицей`);
        assert.match(
            source,
            /<\/div>\s*\{paged\.pageCount > 1 && \(\s*<div className="mt-3">\s*<IosPager page=\{paged\.page\} pageCount=\{paged\.pageCount\}/,
            `${name}: пейджер вне прокрутки таблицы, номер — прижатый`,
        );
    }
});

test('страница сотрудника ищет человека во всём списке, а не на текущей странице', () => {
    assert.match(directorySource(), /return renderEmployeeCardPage\(\{\n\s+rows: allRows,/);
    assert.match(employeesSource(), /renderEmployeeCardPage\(\{\n\s+rows: operatorUsers,/);
});

test('сброс на первую страницу — от списка, вкладки, отдела, поиска и порядка, но не от страницы сотрудника', () => {
    const effect = between(APP, 'useLayoutEffect(() => {\n                setEmployeeListPage(1);', ']);');
    for (const dep of [
        'view', 'isMobileShell',
        'activeUserTab', 'manageUsersDeptFilter', 'manageUsersSearchQuery',
        'activeSvTab', 'supervisorSearchQuery', 'svListDeptFilter',
        'activeTrainerTab', 'trainerSearchQuery',
        'activeAdminTab', 'adminSearchQuery',
        'activeTab', 'manageOperatorsDeptFilter', 'manageOperatorsSearchQuery',
        'usersSortField', 'usersSortDirection', 'sortField', 'sortDirection',
    ]) {
        assert.match(effect, new RegExp(`\\b${dep}\\b`), `нет ${dep} — смена не вернёт на первую страницу`);
    }
    // «Назад» со страницы сотрудника приводит на ту же страницу списка.
    assert.doesNotMatch(effect, /employeeCardId|employeeTableSection/);
    assert.equal(APP.split('setEmployeeListPage(1)').length - 1, 1, 'страницу сбрасывает одно место');
});

test('телефон: тот же номер страницы, «Выбрать всех» — все найденные', () => {
    const phoneProps = between(APP, '<EmployeesMobileView\n', '/>');
    assert.match(phoneProps, /page=\{employeeListPage\}/);
    assert.match(phoneProps, /onPage=\{goEmployeeListPage\}/);
    assert.match(PHONE, /const paged = pageOfEmployeeGroups\(groups, page\);/);
    assert.match(PHONE, /\) : paged\.groups\.map\(\(group\) => \(/);
    assert.match(PHONE, /\{group\.title\} · \{group\.total\}/);
    assert.match(PHONE, /const visibleIds = groups\n/);
    assert.equal(PHONE.split('<IosPager').length - 1, 1);
    assert.match(PHONE, /<IosPager page=\{paged\.page\} pageCount=\{paged\.pageCount\}/);
    assert.match(PHONE, /onPage=\{turnPage\}/);
});
