// «Графики работы»: отдел, открытый поимённо с правами СВ этого отдела.
//
// Решение владельца 23.10.2026: главе ООЗ открыть графики Тез КЦ на просмотр и
// правку, «как СВ этого отдела». Список отделов сервер присылает в ответе
// /api/work_schedules/operators (granted_departments): справочник отделов главе
// режется до её отдела, и без этого списка выбор отдела в шапке Тез КЦ бы не
// предложил, а фильтр по отделу спрятал бы его строки.
//
// Исполняются настоящие объявления из App.jsx — App.jsx монолитен и не
// импортируется, а переписать логику в тест значило бы проверять копию.
//
// Запуск: node --test tests/work_schedule_granted_departments.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { isSupervisorRole } from '../src/utils/roles.js';

const appSource = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const declarationOf = (name) => {
    const at = appSource.indexOf(`const ${name} `);
    assert.ok(at >= 0, `объявление ${name} не найдено — проверь тест`);
    assert.equal(appSource.indexOf(`const ${name} `, at + 1), -1, `объявление ${name} не одно`);
    let depth = 0;
    for (let i = at; i < appSource.length; i += 1) {
        const ch = appSource[i];
        if (ch === '(' || ch === '[' || ch === '{') depth += 1;
        else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
        else if (ch === ';' && depth === 0) return appSource.slice(at, i + 1);
    }
    throw new Error(`не нашёл конец объявления ${name} — проверь тест`);
};

const evaluate = (name, scope) => {
    const names = Object.keys(scope);
    // eslint-disable-next-line no-new-func
    return new Function(...names, `${declarationOf(name)}\nreturn ${name};`)(...names.map(key => scope[key]));
};

const hooks = {
    useMemo: (fn) => fn(),
    useCallback: (fn) => fn,
    normalizeDepartmentCode: (value) => String(value || '').trim().toLowerCase(),
};

const OOZ = { id: 2008, name: 'ООЗ - Отдел Обработки Запросов', code: 'request_processing_department' };
const TEZ = { id: 560, name: 'Тез КЦ', code: 'tez' };

const isPlannerSupervisorRow = evaluate('isPlannerSupervisorRow', { isSupervisorRole });

test('выбор отдела: Тез КЦ появляется, как только пришли его строки', () => {
    const options = (operators, granted) => evaluate('plannerDepartmentOptions', {
        ...hooks, departments: [OOZ], plannerGrantedDepartments: granted, operators,
    });
    const operators = [{ id: 523, department_id: 2008 }, { id: 1001, department_id: 560 }];
    assert.deepEqual(options(operators, [TEZ]).map(opt => [opt.value, opt.code]), [
        ['2008', 'request_processing_department'],
        ['560', 'tez'],
    ]);
    // Без допуска прежнее поведение: только справочник.
    assert.deepEqual(options(operators, []).map(opt => opt.value), ['2008']);
    // Отдел без единой строки не предлагается — как и раньше.
    assert.deepEqual(options([{ id: 523, department_id: 2008 }], [TEZ]).map(opt => opt.value), ['2008']);
    // Отдел из справочника и из допуска — один пункт.
    const both = evaluate('plannerDepartmentOptions', {
        ...hooks, departments: [OOZ, TEZ], plannerGrantedDepartments: [TEZ], operators,
    });
    assert.equal(both.filter(opt => opt.value === '560').length, 1);
});

test('ответ сервера: список допуска запоминается, мусор отбрасывается', () => {
    let state = [{ id: 1, name: 'старое', code: 'x' }];
    const setPlannerGrantedDepartments = (updater) => { state = updater(state); };
    const remember = evaluate('rememberPlannerGrantedDepartments', {
        ...hooks, setPlannerGrantedDepartments,
    });
    remember(undefined);
    assert.deepEqual(state, [{ id: 1, name: 'старое', code: 'x' }], 'старый сервер без поля список не сбрасывает');
    remember([{ id: '560', name: 'Тез КЦ', code: ' TEZ ' }, { id: null }, { id: 'abc' }, { id: -4 }]);
    assert.deepEqual(state, [{ id: 560, name: 'Тез КЦ', code: 'tez' }]);
    const sameList = state;
    remember([{ id: 560, name: 'Тез КЦ', code: 'tez' }]);
    assert.equal(state, sameList, 'тот же список — тот же объект, без лишней перерисовки');
    remember([]);
    assert.deepEqual(state, []);
});

test('строки СВ отдела допуска заперты, операторов — нет', () => {
    const lockedFor = ({ plain = false, granted = ['560'] } = {}) => evaluate('isPlannerSupervisorRowLocked', {
        isPlannerSupervisorRow,
        plannerViewerIsPlainSupervisor: plain,
        plannerGrantedDepartmentIds: new Set(granted),
    });
    const locked = lockedFor();
    assert.equal(locked({ id: 3, role: 'sv', department_id: 560 }), true, 'СВ Тез КЦ');
    assert.equal(locked({ id: 1001, role: 'operator', department_id: 560 }), false, 'оператор Тез КЦ');
    assert.equal(locked({ id: 77, role: 'sv', department_id: 2008 }), false, 'СВ своего отдела глава правит');
    assert.equal(locked(undefined), false);
    // Рядовой СВ — прежнее правило: строки СВ только смотреть.
    assert.equal(lockedFor({ plain: true, granted: [] })({ id: 77, role: 'sv', department_id: 1 }), true);
    assert.equal(lockedFor({ plain: false, granted: [] })({ id: 3, role: 'sv', department_id: 560 }), false);
});

test('«Убрать из ЧС» в отделе допуска не показывается', () => {
    const canRemove = (operator, viewerCan = true) => evaluate('plannerModalCanRemoveBlacklist', {
        plannerViewerCanRemoveBlacklist: viewerCan,
        plannerGrantedDepartmentIds: new Set(['560']),
        plannerModalOperator: operator,
    });
    assert.equal(canRemove({ id: 1001, department_id: 560 }), false, 'оператор Тез КЦ');
    assert.equal(canRemove({ id: 523, department_id: 2008 }), true, 'оператор своего отдела');
    assert.equal(canRemove(undefined), true, 'окно ещё без оператора — как раньше');
    assert.equal(canRemove({ id: 523, department_id: 2008 }, false), false, 'рядовой СВ — как раньше');
});

test('запирание и флаг ЧС подключены там, где решают показ', () => {
    assert.match(
        declarationOf('isPlannerRowLocked'),
        /isPlannerSupervisorRowLocked\(operators\.find\(o => String\(o\?\.id\) === String\(opId\)\)\)/,
    );
    assert.ok(appSource.includes('modalActiveScheduleStatus?.isBlacklist && plannerModalCanRemoveBlacklist ? ('));
    assert.ok(appSource.includes(
        'modalActiveScheduleStatus.isBlacklist && plannerModalCanRemoveBlacklist && !plannerReadOnly ? ('));
    // Оба запроса списка сотрудников раздела сообщают отделы допуска.
    assert.equal(appSource.split('rememberPlannerGrantedDepartments(data?.granted_departments);').length - 1, 2);
});
