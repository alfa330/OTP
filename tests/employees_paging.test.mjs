/*
 * «Учет сотрудников» — списки по страницам с сервера.
 *
 * Страницу, числа на метках и группы считает сервер (employees/page.py,
 * tests/test_employees_page_postgres.py). Здесь — то, что живёт в браузере:
 * строка запроса, разметка ответа, когда запрос уходит, отменяется и
 * повторяется (employeesPaging.js), и проводка в App.jsx и
 * EmployeesMobileView.jsx — она ломается молча при правке разметки, поэтому
 * сторожится текстом.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
    EMPLOYEES_PAGE_SIZE,
    createPageLoader,
    employeeListParamsFor,
    employeesPageQuery,
    groupPageRows,
    idsQueryOf,
    listKeyOf,
    pageOfResponse,
} from '../src/components/employees/employeesPaging.js';

const here = dirname(fileURLToPath(import.meta.url));
// Git for Windows кладёт файлы в CRLF — сверяем с LF, как соседние тесты.
const read = (...parts) => readFileSync(join(here, '..', ...parts), 'utf8').split('\r\n').join('\n');
const APP = read('src', 'App.jsx');
const PHONE = read('src', 'components', 'employees', 'EmployeesMobileView.jsx');
const HOOK = read('src', 'components', 'employees', 'useEmployeesPage.js');

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
const paramsSource = () => between(APP, 'const employeeListParams = employeeListParamsFor(view, {', '});');

const parse = (query) => Object.fromEntries(new URLSearchParams(query));

test('страница — 50 строк, как в «Сессиях» и «Касаниях»', () => {
    assert.equal(EMPLOYEES_PAGE_SIZE, 50);
    assert.deepEqual(parse(employeesPageQuery({ page: 3 })), {
        list: 'users', status: 'active', sort: 'name', dir: 'asc', limit: '50', offset: '100',
    });
});

test('строка запроса: роли по алфавиту, поиск без пробелов, пустой отдел не уходит', () => {
    const query = employeesPageQuery({
        list: 'users', roles: ['trainee', 'operator'], status: 'fired', department: '7', search: '  Иван ',
        sort: 'hire_date', dir: 'desc', group: 'operators', page: '2.9', ids: true,
    });
    assert.deepEqual(parse(query), {
        list: 'users', roles: 'operator,trainee', status: 'fired', department: '7', q: 'Иван',
        sort: 'hire_date', dir: 'desc', group: 'operators', limit: '50', offset: '50', ids: '1',
    });
    assert.equal(employeesPageQuery({ roles: ['b', 'a'] }), employeesPageQuery({ roles: ['a', 'b'] }));
    const plain = parse(employeesPageQuery({ department: '', search: '   ', page: 0 }));
    assert.ok(!('department' in plain) && !('q' in plain) && !('roles' in plain));
    assert.equal(plain.offset, '0');
    // Пустой набор ролей — «никого», а не «все роли».
    assert.equal(parse(employeesPageQuery({ roles: [] })).roles, '');
});

test('ответ → страница: номер — тот, что отдал сервер', () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({ id: i + 1 }));
    const page = pageOfResponse({
        employees: rows, total: 312, list_total: 340, offset: 300, limit: 50,
        status_counts: { active: 312, fired: 28 },
    });
    assert.deepEqual(
        [page.page, page.pageCount, page.total, page.from, page.to, page.listTotal],
        [7, 7, 312, 301, 312, 340],
    );
    assert.deepEqual(page.counts, { active: 312, fired: 28 });
    assert.equal(page.groups, null);
    const empty = pageOfResponse(null);
    assert.deepEqual([empty.page, empty.pageCount, empty.total, empty.from, empty.to], [1, 1, 0, 0, 0]);
    assert.deepEqual(empty.rows, []);
});

test('группы: шапка на каждой странице, где есть её люди, число — вся группа', () => {
    const groups = [
        { key: 'mine', title: 'Мои операторы', total: 30 },
        { key: 'dir:Линия', title: 'Линия', total: 45 },
        { key: 'dir:Чаты', title: 'Чаты', total: 10 },
    ];
    const rows = [
        ...Array.from({ length: 5 }, (_, i) => ({ id: `l${i}`, list_group: 'dir:Линия' })),
        ...Array.from({ length: 3 }, (_, i) => ({ id: `c${i}`, list_group: 'dir:Чаты' })),
    ];
    assert.deepEqual(groupPageRows(rows, groups).map((g) => [g.key, g.title, g.rows.length, g.total]), [
        ['dir:Линия', 'Линия', 5, 45],
        ['dir:Чаты', 'Чаты', 3, 10],
    ]);
    // Порядок строк — тот, что пришёл с сервера.
    assert.deepEqual(groupPageRows(rows, groups).flatMap((g) => g.rows.map((r) => r.id)), rows.map((r) => r.id));
    assert.deepEqual(groupPageRows([], groups), []);
});

test('ключ списка: роли, группа и учётка различают списки, порядок ролей — нет', () => {
    const trainers = { list: 'users', roles: ['trainer'] };
    assert.notEqual(listKeyOf(trainers, 7), listKeyOf({ list: 'users', roles: ['admin'] }, 7), '«Тренеры» и «Админы»');
    assert.notEqual(listKeyOf({ list: 'users', roles: ['operator'] }, 7),
        listKeyOf({ list: 'users', roles: ['operator'], group: 'operators' }, 7), '«Сотрудники» и «Операторы»');
    assert.notEqual(listKeyOf(trainers, 7), listKeyOf(trainers, 8), 'вошли другим в той же вкладке');
    assert.notEqual(listKeyOf({ list: 'supervisors' }, 7), listKeyOf({ list: 'users' }, 7));
    assert.equal(listKeyOf({ list: 'users', roles: ['b', 'a'] }, 7), listKeyOf({ list: 'users', roles: ['a', 'b'] }, 7));
    // Поиск, вкладка и страница — тот же список: прежняя страница остаётся на экране, пока идёт запрос.
    assert.equal(listKeyOf({ ...trainers, search: 'ив', status: 'fired', page: 3 }, 7), listKeyOf(trainers, 7));
    assert.equal(listKeyOf(null, 7), null);
});

test('«Выбрать всех»: id всех найденных — те же фильтры, первая и единственная строка', () => {
    const query = employeesPageQuery({ roles: ['operator'], status: 'fired', department: 4, search: 'ив', page: 3 });
    assert.deepEqual(parse(idsQueryOf(query)), { ...parse(query), ids: '1', limit: '1', offset: '0' });
});

// Состояние App.jsx — у каждого поля своё значение, чтобы перепутанное было видно.
const LIST_STATE = {
    userId: 7,
    embed: false,
    showsAdminLists: true,
    showsAdmins: true,
    showsOperators: true,
    staffRoles: ['operator', 'trainee'],
    operatorRoles: ['operator', 'sv'],
    page: 3,
    usersSort: { field: 'hire_date', dir: 'desc' },
    operatorsSort: { field: 'rate', dir: 'asc' },
    staff: { status: 'bs', department: '11', search: 'staff' },
    sv: { status: 'fired', department: '12', search: 'sv' },
    trainers: { status: 'working', search: 'trainers' },
    admins: { status: 'annual_leave', search: 'admins' },
    operators: { status: 'sick_leave', department: '13', search: 'operators' },
};

test('каждый вид раздела просит свой список со своими вкладкой, отделом, поиском, порядком и страницей', () => {
    const usersOrder = { sort: 'hire_date', dir: 'desc', page: 3 };
    const expected = {
        sv_list: { list: 'supervisors', status: 'fired', department: '12', search: 'sv', ...usersOrder },
        manage_users: { list: 'users', roles: ['operator', 'trainee'], status: 'bs', department: '11', search: 'staff', ...usersOrder },
        employees: { list: 'users', roles: ['operator', 'trainee'], status: 'bs', department: '11', search: 'staff', ...usersOrder },
        manage_trainers: { list: 'users', roles: ['trainer'], status: 'working', search: 'trainers', ...usersOrder },
        manage_admins: { list: 'users', roles: ['admin'], status: 'annual_leave', search: 'admins', ...usersOrder },
        manage_operators: {
            list: 'users', roles: ['operator', 'sv'], group: 'operators', status: 'sick_leave', department: '13',
            search: 'operators', sort: 'rate', dir: 'asc', page: 3,
        },
        hours: null,
    };
    for (const [view, params] of Object.entries(expected)) {
        assert.deepEqual(employeeListParamsFor(view, LIST_STATE), params, view);
    }
});

test('чужой раздел запрос не шлёт: те же условия, под которыми список рисуется', () => {
    assert.equal(employeeListParamsFor('manage_users', { ...LIST_STATE, userId: null }), null, 'не вошёл');
    assert.equal(employeeListParamsFor('manage_users', { ...LIST_STATE, embed: true }), null, 'окно «Чатов ОП» в CRM');
    for (const view of ['sv_list', 'manage_users', 'employees', 'manage_trainers', 'manage_admins']) {
        assert.equal(employeeListParamsFor(view, { ...LIST_STATE, showsAdminLists: false }), null, view);
    }
    assert.equal(employeeListParamsFor('manage_admins', { ...LIST_STATE, showsAdmins: false }), null);
    assert.equal(employeeListParamsFor('manage_operators', { ...LIST_STATE, showsOperators: false }), null);
});

// ─── Когда запрос уходит ───

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const stand = () => {
    let clock = 0;
    const calls = [];
    const events = [];
    const loader = createPageLoader({
        now: () => clock,
        load: (query, { signal }) => new Promise((resolve, reject) => {
            const call = { query, signal, resolve, reject };
            signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'CanceledError' })));
            calls.push(call);
        }),
        onData: (listKey, data) => events.push(['data', listKey, data]),
        onLoading: (value) => events.push(['loading', value]),
        onError: (error, listKey) => events.push(['error', error.message, listKey]),
        onPageMoved: (page) => events.push(['moved', page]),
    });
    return {
        loader, calls, events,
        tick: (ms) => { clock += ms; },
        answer: async (call, data) => { call.resolve(data); await flush(); },
    };
};

test('запрос уходит один раз, повтор того же — не уходит', async () => {
    const s = stand();
    const q = employeesPageQuery({ page: 1 });
    s.loader.sync(q, 'users');
    s.loader.sync(q, 'users');
    await flush();
    assert.equal(s.calls.length, 1);
    await s.answer(s.calls[0], { employees: [], total: 0, offset: 0, limit: 50 });
    s.loader.sync(q, 'users');
    await flush();
    assert.equal(s.calls.length, 1, 'страница уже на экране');
});

test('новый запрос отменяет прежний, и прежний не пишет на экран', async () => {
    const s = stand();
    s.loader.sync(employeesPageQuery({ search: 'Ив' }), 'users');
    await flush();
    s.loader.sync(employeesPageQuery({ search: 'Иванов' }), 'users');
    await flush();
    assert.equal(s.calls.length, 2);
    assert.ok(s.calls[0].signal.aborted);
    await s.answer(s.calls[1], { employees: [{ id: 2 }], total: 1, offset: 0, limit: 50 });
    assert.deepEqual(s.events.filter((e) => e[0] === 'data').map((e) => e[2].employees[0].id), [2]);
    assert.ok(!s.events.some((e) => e[0] === 'error'), 'отмена — не ошибка');
});

test('сброс номера страницы в том же такте не пускает в сеть запрос прежней страницы', async () => {
    const s = stand();
    s.loader.sync(employeesPageQuery({ status: 'fired', page: 5 }), 'users');
    s.loader.sync(employeesPageQuery({ status: 'fired', page: 1 }), 'users');
    await flush();
    assert.deepEqual(s.calls.map((c) => parse(c.query).offset), ['0']);
});

test('после правки страница перечитывается, а от запроса при входе — нет', async () => {
    const s = stand();
    const q = employeesPageQuery({});
    s.tick(10);
    const loginStamp = 5; // полный список запросили при входе, раньше страницы
    s.loader.sync(q, 'users', 0);
    await flush();
    s.loader.sync(q, 'users', loginStamp);
    await flush();
    assert.equal(s.calls.length, 1, 'ответ на запрос при входе не повторяет страницу');
    await s.answer(s.calls[0], { employees: [], total: 0, offset: 0, limit: 50 });
    s.loader.sync(q, 'users', loginStamp);
    await flush();
    assert.equal(s.calls.length, 1);
    s.tick(100);
    s.loader.sync(q, 'users', 50); // правка: полный список перечитан после нашей страницы
    await flush();
    assert.equal(s.calls.length, 2);
});

test('сервер прижал номер к последней странице — номер переезжает, запрос не повторяется', async () => {
    const s = stand();
    s.loader.sync(employeesPageQuery({ page: 5 }), 'users');
    await flush();
    await s.answer(s.calls[0], { employees: [{ id: 1 }], total: 120, offset: 100, limit: 50 });
    assert.deepEqual(s.events.filter((e) => e[0] === 'moved'), [['moved', 3]]);
    s.loader.sync(employeesPageQuery({ page: 3 }), 'users');
    await flush();
    assert.equal(s.calls.length, 1);
});

test('список ушёл с экрана — при возвращении перечитывается', async () => {
    const s = stand();
    const q = employeesPageQuery({});
    s.loader.sync(q, 'users');
    await flush();
    await s.answer(s.calls[0], { employees: [], total: 0, offset: 0, limit: 50 });
    s.loader.sync(null, null);
    s.loader.sync(q, 'users');
    await flush();
    assert.equal(s.calls.length, 2);
});

test('ошибка сервера доходит до тоста и снимает «загрузку»', async () => {
    const s = stand();
    s.loader.sync(employeesPageQuery({}), 'users');
    await flush();
    s.calls[0].reject(new Error('500'));
    await flush();
    assert.deepEqual(s.events.slice(-2), [['loading', false], ['error', '500', 'users']]);
});

test('вернулись к показанной странице, пока шла другая — та отменена и на экран не попадает', async () => {
    const s = stand();
    const first = employeesPageQuery({ page: 1 });
    s.loader.sync(first, 'users');
    await flush();
    await s.answer(s.calls[0], { employees: [{ id: 'p1' }], total: 120, offset: 0, limit: 50 });
    s.loader.sync(employeesPageQuery({ page: 2 }), 'users');
    await flush();
    s.loader.sync(first, 'users');
    await flush();
    assert.ok(s.calls[1].signal.aborted, 'запрос второй страницы отменён');
    assert.equal(s.calls.length, 2, 'первая страница уже на экране — повторно не читается');
    assert.deepEqual(s.events.filter((e) => e[0] === 'data').map((e) => e[2].employees[0].id), ['p1']);
    assert.deepEqual(s.events.at(-1), ['loading', false]);
});

test('другая страница не загрузилась — номер возвращается к показанной, нажатие того же номера шлёт запрос', async () => {
    const s = stand();
    s.loader.sync(employeesPageQuery({ page: 1 }), 'users');
    await flush();
    await s.answer(s.calls[0], { employees: [{ id: 'p1' }], total: 120, offset: 0, limit: 50 });
    s.loader.sync(employeesPageQuery({ page: 2 }), 'users');
    await flush();
    s.calls[1].reject(new Error('500'));
    await flush();
    assert.deepEqual(s.events.filter((e) => e[0] === 'moved'), [['moved', 1]]);
    // Не загрузилась другая вкладка — номер страницы не при чём, он не двигается.
    s.loader.sync(employeesPageQuery({ status: 'fired', page: 1 }), 'users');
    await flush();
    s.calls[2].reject(new Error('500'));
    await flush();
    assert.equal(s.events.filter((e) => e[0] === 'moved').length, 1);
});

test('«Повторить» шлёт тот же запрос заново, даже если он уже на экране', async () => {
    const s = stand();
    const q = employeesPageQuery({});
    s.loader.sync(q, 'users');
    await flush();
    await s.answer(s.calls[0], { employees: [], total: 0, offset: 0, limit: 50 });
    s.loader.retry(q, 'users');
    await flush();
    assert.deepEqual(s.calls.map((c) => c.query), [q, q]);
});

// ─── Проводка ───

test('компьютер: каждый из пяти списков рисует страницу с сервера одним пейджером', () => {
    const directory = directorySource();
    assert.match(directory, /const listPage = employeesPage\.page;/);
    assert.match(directory, /\{listPage\.rows\.map\(\(employee\) => \(/);
    assert.match(directory, /const count = listPage\?\.counts\?\.\[tab\.key\] \?\? 0;/);
    assert.doesNotMatch(directory, /\.sort\(\(a, b\) => compareUsersByField|isEmployeeVisibleByStatusTab\(/,
        'список снова фильтруется и сортируется в браузере');

    const employees = employeesSource();
    assert.match(employees, /const paged = employeesPage\.page;/);
    assert.match(employees, /const grouped = \{ __all__: paged\.rows \};/);
    assert.match(employees, /const count = counts\[tab\.key\] \?\? 0;/);
    assert.doesNotMatch(employees, /compareUsersByField|isEmployeeVisibleByStatusTab\(|filteredUsers/);

    // «Пусто» — только когда пуст весь список (вкладка): поиск без совпадений
    // оставляет таблицу и поле поиска, иначе введённое не стереть.
    assert.match(directory, /\) : listPage\.listTotal === 0 \? \(\s*<p className="text-center text-gray-600">\{emptyText\}<\/p>/);
    assert.match(directory, /\{listPage\.total === 0 \? \(\s*<p className="px-6 py-6 text-center text-gray-600">\{emptySearchText\}<\/p>/);
    assert.match(employees, /if \(\(paged\.counts\[activeUserTab\] \?\? 0\) === 0\) \{/);

    const operators = operatorsSource();
    assert.match(operators, /if \(\(paged\.counts\[activeTab\] \?\? 0\) === 0\) \{/);
    // Число в шапке группы — вся группа с сервера, а не её кусок на этой странице.
    assert.match(operators, /Мои операторы <span className="ml-2 text-xs text-gray-500">\(\{pagedMine\.total\}\)<\/span>/);
    assert.match(operators, /\{group\.title\} <span className="ml-2 text-xs text-gray-500">\(\{group\.total\}\)<\/span>/);
    assert.match(operators, /const pagedGroups = groupPageRows\(paged\.rows, paged\.groups\);/);
    assert.match(operators, /\{pagedMine\.rows\.map\(\(op\) => \(/);
    assert.match(operators, /\{pagedDirections\.map\(\(group\) => \(/);
    assert.doesNotMatch(operators, /myOperatorsSorted|sortedDirectionNames|isEmployeeVisibleByStatusTab\(/,
        'группы снова считаются в браузере');

    for (const [name, source, page] of [['directory', directory, 'listPage'], ['employees', employees, 'paged'],
        ['operators', operators, 'paged']]) {
        assert.equal(source.split('<IosPager').length - 1, 1, `${name}: ровно один пейджер`);
        assert.match(source, /onPage=\{goEmployeeListPage\}/, name);
        assert.match(source, new RegExp(`\\{${page}\\.pageCount > 1 && \\(`), `${name}: на одной странице пейджера нет`);
        // Итог в подвале таблицы — только когда пейджера нет, иначе одно число дважды.
        assert.match(source, new RegExp(`\\{${page}\\.pageCount === 1 && <>\\{${page}\\.total\\}`), `${name}: счёт в подвале`);
        assert.match(source, /<div ref=\{employeeListTopRef\} className="overflow-x-auto/, `${name}: якорь прокрутки`);
        assert.ok(source.indexOf('<IosPager') > source.lastIndexOf('</table>'), `${name}: пейджер под таблицей`);
        assert.match(
            source,
            new RegExp(`</div>\\s*\\{${page}\\.pageCount > 1 && \\(\\s*<div className="mt-3">\\s*<IosPager page=\\{${page}\\.page\\} pageCount=\\{${page}\\.pageCount\\}`),
            `${name}: пейджер вне прокрутки таблицы, номер — тот, что отдал сервер`,
        );
        assert.match(source, /aria-busy=\{employeesPage\.loading \? 'true' : undefined\}/, `${name}: таблица занята, пока идёт запрос`);
        // Первой страницы ещё нет — «Загрузка…» или сбой с «Повторить».
        assert.match(source, /\{!(listPage|employeesPage\.page) \? \(\s*renderEmployeeListPending\(\)\s*\) :/, `${name}: ожидание первой страницы`);
        // Ответ задержался — строки гаснут, как список на телефоне и в «Сессиях»; быстрый ответ не мигает.
        assert.match(source, /<tbody className=\{`bg-white divide-y divide-gray-200 transition-opacity duration-200\$\{employeesPage\.loading \? ' opacity-60 delay-150' : ''\}`\}>/, `${name}: строки гаснут на долгом запросе`);
    }
});

test('страница сотрудника ищет человека во всём списке, а не на текущей странице', () => {
    assert.match(directorySource(), /return renderEmployeeCardPage\(\{\n\s+rows: allRows,/);
    assert.match(employeesSource(), /renderEmployeeCardPage\(\{\n\s+rows: operatorUsers,/);
});

test('App.jsx отдаёт каждому списку его собственные вкладку, отдел, поиск, порядок и страницу', () => {
    const params = paramsSource();
    for (const line of [
        'userId: user?.id,',
        'embed: CHATS_EMBED,',
        'showsAdminLists: isAdminLikeRole || isDepartmentHeadAdminEmployeeView || isEmployeeAccountingManagerView,',
        'showsAdmins: isSuperAdmin || isEmployeeAccountingManager,',
        'showsOperators: isDepartmentManager || isPlainTrainer,',
        'staffRoles: manageOperatorRoleList.filter(isRankAndFileRole),',
        'operatorRoles: manageOperatorRoleList,',
        'page: employeeListPage,',
        'usersSort: { field: usersSortField, dir: usersSortDirection },',
        'operatorsSort: { field: sortField, dir: sortDirection },',
        'staff: { status: activeUserTab, department: manageUsersDeptFilter, search: manageUsersSearchQuery },',
        'sv: { status: activeSvTab, department: svListDeptFilter, search: supervisorSearchQuery },',
        'trainers: { status: activeTrainerTab, search: trainerSearchQuery },',
        'admins: { status: activeAdminTab, search: adminSearchQuery },',
        'operators: { status: activeTab, department: manageOperatorsDeptFilter, search: manageOperatorsSearchQuery },',
    ]) {
        assert.ok(params.includes(`\n                ${line}\n`), line);
    }
    assert.equal(params.split('\n').filter((line) => line.trim()).length, 16, 'лишнее или пропавшее поле состояния');
    // Набор ролей у страницы и у полного списка — одно правило.
    assert.match(APP, /const manageOperatorRoles = new Set\(manageOperatorRoleList\);/);
    assert.equal(APP.split('useEmployeesPage({').length - 1, 1, 'одна страница на раздел');
    assert.match(APP, /onPageMoved: setEmployeeListPage,/);
    assert.match(APP, /`\$\{API_BASE_URL\}\/api\/admin\/employees\/page\?\$\{query\}`/);
});

test('правка сотрудника перечитывает страницу: отметка ставится, когда полный список перечитан', () => {
    const fetchUsers = between(APP, 'const fetchUsers = async ({ initial = false } = {}) => {', 'const fetchAdminSessions = useCallback(');
    assert.match(fetchUsers, /const startedAt = performance\.now\(\);/);
    assert.match(fetchUsers, /if \(!initial\) setEmployeeListStamp\(startedAt\);/);
    const fetchSvList = between(APP, 'const fetchSvList = async ({ initial = false } = {}) => {', 'const svDataUrl = ');
    assert.match(fetchSvList, /if \(!initial\) setEmployeeListStamp\(startedAt\);/);
    assert.match(APP, /refreshStamp: employeeListStamp,/);
    // Загрузка при входе — не правка: страница от неё второй раз не читается.
    const login = between(APP, 'if (!user || !user.id || CHATS_EMBED) return;\n\n                if (isAdminLikeRole) {', 'fetchSensitiveAccessStatus();');
    assert.doesNotMatch(login, /fetch(Users|SvList)\(\)/, 'загрузка при входе без initial перечитает страницу второй раз');
    assert.equal(login.split('fetchUsers({ initial: true });').length - 1, 4);
    assert.equal(login.split('fetchSvList({ initial: true });').length - 1, 3);
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

test('поиск уходит на сервер, когда перестали печатать; очистка и смена списка — сразу', () => {
    assert.match(HOOK, /setTimeout\(\(\) => setSettled\(\{ listKey, search \}\), EMPLOYEES_SEARCH_DELAY_MS\)/);
    assert.match(HOOK, /if \(settled\.listKey !== listKey \|\| !search\.trim\(\)\) \{/);
    // Пустой поиск — в этом же кадре: иначе со второй страницы ушла бы первая со старым поиском.
    assert.match(HOOK, /const settledSearch = settled\.listKey !== listKey \|\| !search\.trim\(\) \? search : settled\.search;/);
    // Пока печатают, запрос не уходит: первая буква сбрасывает номер страницы, и
    // иначе ушла бы первая страница со старым поиском.
    assert.match(HOOK, /const typing = settled\.listKey === listKey && search !== settled\.search && Boolean\(search\.trim\(\)\);/);
    // Правка сотрудника (refreshStamp) перечитывает страницу; ушли со списка — его страница забыта.
    assert.match(HOOK, /if \(typing\) return;\n\s+if \(!query\) setState\(NO_PAGE\);\n\s+loaderRef\.current\.sync\(query, listKey, refreshStamp\);\n\s+\}, \[query, refreshStamp, typing\]\);/);
    assert.match(HOOK, /const ready = state\.data != null && state\.listKey === listKey;/);
    assert.match(HOOK, /const listKey = listKeyOf\(params, owner\);/);
    assert.match(HOOK, /callbacksRef\.current\.load\(idsQueryOf\(query\), \{\}\)/);
    assert.match(APP, /const employeesPage = useEmployeesPage\(\{\n\s+owner: user\?\.id,/);
    // Упавший запрос — не вечная «Загрузка»: список говорит, что не загрузился, и даёт повторить.
    assert.match(HOOK, /const failed = !ready && !loading && listKey != null && failedListKey === listKey;/);
    assert.match(HOOK, /if \(query\) loaderRef\.current\.retry\(query, listKey\);/);
    const pending = between(APP, 'const renderEmployeeListPending = () => (', '// «Выбрать всех» на телефоне');
    assert.match(pending, /\{employeesPage\.failed \? \(/);
    assert.match(pending, /Не удалось загрузить список\./);
    assert.match(pending, /onClick=\{employeesPage\.retry\}/);
    assert.match(pending, /\) : 'Загрузка\.\.\.'\}/);
    assert.match(PHONE, /\{!ready && failed && \(/);
    assert.match(PHONE, /\{onRetry && <button type="button" className="emp-m-link" onClick=\{onRetry\}>Повторить<\/button>\}/);
    assert.match(PHONE, /\{!ready && !failed && 'Загрузка…'\}/);
});

test('телефон: та же страница с сервера, «Выбрать всех» — все найденные', () => {
    const phoneProps = between(APP, '<EmployeesMobileView\n', '/>');
    assert.match(phoneProps, /pageData=\{employeesPage\.page\}/);
    assert.match(phoneProps, /ready=\{employeesPage\.ready\}/);
    assert.match(phoneProps, /loading=\{employeesPage\.loading\}/);
    assert.match(phoneProps, /onPage=\{goEmployeeListPage\}/);
    assert.match(phoneProps, /onRetry=\{employeesPage\.retry\}/);
    assert.ok(phoneProps.indexOf('{...config}') < phoneProps.indexOf('pageData='), 'страницу не перекрыть настройками списка');
    // Разбор страницы — phonePage (employeesPhoneList.js, свои тесты).
    assert.match(PHONE, /const \{ rowsById, counts, total, listTotal, paged, allSelected \} = phonePage\(\{ pageData, rows, selectedIds \}\);/);
    assert.match(PHONE, /\) : paged\.groups\.map\(\(group\) => \(/);
    assert.match(PHONE, /\{group\.title\} · \{group\.total\}/);
    assert.equal(PHONE.split('<IosPager').length - 1, 1);
    assert.match(PHONE, /<IosPager page=\{paged\.page\} pageCount=\{paged\.pageCount\}/);
    assert.match(PHONE, /onPage=\{turnPage\}/);
    assert.match(PHONE, /selection\.onSelectAll\(\)\)/);
    assert.match(APP, /onSelectAll: selectAllFoundManageUsers,/);
    assert.match(APP, /setSelectedManageUsersIds\(new Set\(await employeesPage\.fetchIds\(\)\)\);/);
    assert.doesNotMatch(PHONE, /countByStatus|filterEmployees|groupOperators|matchesSearch|isVisibleByStatus/,
        'телефон снова фильтрует список сам');
});
