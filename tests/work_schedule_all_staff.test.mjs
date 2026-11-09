// «Графики работы» для всех сотрудников — решение владельца 09.11.2026: «по всем
// нужно сделать возможность вести графики в Графиках работы».
//
// Строки не-операторов (руководители, бэк-офис, отделы-проекты) ведутся так же,
// как строки СВ из задачи #352: без операторских действий и с ручными перерывами.
// Строка без направления подписывается группой (у отделов-проектов направлений
// нет, люди делятся группами), а без группы — должностью.
//
// Исполняются настоящие объявления из App.jsx — App.jsx монолитен и не
// импортируется, а переписать логику в тест значило бы проверять копию.
//
// Запуск: node --test tests/work_schedule_all_staff.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { isSupervisorRole, normalizeRole } from '../src/utils/roles.js';

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

const NAMES = [
    'PLANNER_SUPERVISORS_DIRECTION', 'PLANNER_SUPERVISORS_DIRECTION_LABEL', 'isPlannerSupervisorRow',
    'isPlannerLineRow', 'PLANNER_ROLE_GROUP_LABELS', 'PLANNER_GROUP_KEY_PREFIX', 'PLANNER_ROLE_KEY_PREFIX',
    'plannerRowGroupKey', 'plannerGroupKeyLabel', 'isPlannerRealDirectionKey', 'plannerRowDirectionLabel',
];
// eslint-disable-next-line no-new-func
const helpers = new Function('isSupervisorRole', 'normalizeRole',
    `${NAMES.map(declarationOf).join('\n')}\nreturn { ${NAMES.join(', ')} };`)(isSupervisorRole, normalizeRole);

test('строка линии — оператор и старая строка без роли', () => {
    const { isPlannerLineRow } = helpers;
    assert.equal(isPlannerLineRow({ role: 'operator' }), true);
    assert.equal(isPlannerLineRow({ role: ' Operator ' }), true);
    assert.equal(isPlannerLineRow({}), true, 'старая строка без роли — операторская');
    for (const role of ['sv', 'supervisor', 'admin', 'super_admin', 'trainer', 'trainee',
        'hr_manager', 'accounting_manager', 'marketing_manager']) {
        assert.equal(isPlannerLineRow({ role }), false, role);
    }
});

test('подпись строки: направление → «Супервайзеры» → группа → должность', () => {
    const { plannerRowDirectionLabel: label } = helpers;
    assert.equal(label({ role: 'operator', direction: 'Основа', group_name: 'Группа А' }), 'Основа');
    assert.equal(label({ role: 'sv', direction: null }), 'Супервайзеры');
    assert.equal(label({ role: 'operator', group_name: 'Разработчики TT' }), 'Разработчики TT');
    assert.equal(label({ role: 'super_admin' }), 'Администраторы');
    assert.equal(label({ role: 'accounting_manager' }), 'Бухгалтерия');
    assert.equal(label({ role: 'hr_manager', group_name: '  ' }), 'HR', 'пустая группа — не группа');
    assert.equal(label({ role: 'operator' }), '', 'оператор без направления и группы — как раньше');
});

test('служебные ключи не путаются с направлениями', () => {
    const { plannerRowGroupKey: key, isPlannerRealDirectionKey: real, plannerGroupKeyLabel: label } = helpers;
    const groupKey = key({ role: 'operator', group_name: 'Офис TEZ' });
    const roleKey = key({ role: 'trainer' });
    assert.notEqual(groupKey, 'Офис TEZ', 'группа не выдаёт себя за направление с тем же именем');
    assert.equal(label(groupKey), 'Офис TEZ');
    assert.equal(label(roleKey), 'Тренеры');
    assert.equal(real('Основа'), true);
    for (const pseudo of [groupKey, roleKey, key({ role: 'sv' }), '']) {
        assert.equal(real(pseudo), false, String(pseudo));
    }
});

test('фильтр, выгрузка и сортировка идут по ключу строки', () => {
    assert.ok(appSource.includes('filtered = filtered.filter(op => selectedDirections.includes(plannerRowGroupKey(op)));'));
    // В Excel направлением уходит только настоящее направление.
    assert.ok(appSource.includes('if (isPlannerRealDirectionKey(normalized)) {\n                            queryParams.append(\'direction\', normalized);'));
    // Пункты фильтра — направления, затем группы и должности строк без направления.
    assert.match(declarationOf('plannerDirectionOptions'), /plannerExtraGroupKeys\.map\(key => \(\{ value: key, label: plannerGroupKeyLabel\(key\) \}\)\)/);
});

test('операторские действия — только у строк линии', () => {
    // Штраф сервер примет только у оператора; тренинги и тех. сбои — операторские.
    assert.ok(appSource.includes('{!plannerReadOnly && !plannerModalIsNonLineRow && plannerModalCanFine ? ('));
    assert.ok(appSource.includes('modalTabControl && !plannerModalIsNonLineRow && ('));
    // Перерывы не-операторов — ручные, и соседями по раскладке они не считаются.
    assert.ok(appSource.includes('if (op && !isPlannerLineRow(op)) {\n                    const manualBreaks'));
    assert.match(declarationOf('cloneOperatorsForBreakSimulation'), /\.filter\(op => isPlannerLineRow\(op\)\)/);
    // Раскладка перерывов, массовый пересчёт, отчёт соответствия и агрегация дня —
    // только линия (выгрузка Excel проверяет то же через .some).
    assert.equal(appSource.split('.filter(op => isPlannerLineRow(op))').length - 1, 4);
    assert.ok(appSource.includes('!filteredOperators.some(op => isPlannerLineRow(op))'));
    assert.equal(appSource.split('!isPlannerSupervisorRow(op)').length - 1, 0, 'СВ-только фильтров не осталось');
});
