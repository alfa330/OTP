import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { isDepartmentHead, normalizeRole } from '../src/utils/roles.js';

/**
 * Кому виден пункт «Дисциплина» (canAccessDisciplineSectionForUser в src/App.jsx),
 * пока раздел выкладывается по шагам: флаг DISCIPLINE_SUPER_ADMIN_ONLY пускает
 * одного супер-админа, поимённый список DISCIPLINE_ROLLOUT_USER_IDS — ещё и
 * своих людей. С 23.10.2026 в нём 56 — оператор СЗоВ («открой данный раздел для …
 * как оператору»). Список снимает только флаг: дальше работают обычные правила
 * (оператор отдела раздела — да, тот же id в чужом отделе или с ролью админа — нет),
 * поэтому гоняем настоящий предикат таблицей людей: текстовая проверка не
 * заметила бы строку списка, поставленную не туда. Объявления достаём из
 * src/App.jsx: файл монолитный и не импортируется, копия проверяла бы копию.
 * Серверную границу (discipline/access.py) и совпадение списков проверяют
 * tests/test_discipline_rules.py и tests/test_discipline_frontend_wiring.py.
 */
const source = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');

/* Объявление `const NAME ...;` целиком: от имени до точки с запятой на нулевой
   глубине скобок — тело предиката многострочное. */
const declarationOf = (name) => {
    const at = source.indexOf(`const ${name} `);
    assert.ok(at >= 0, `объявление ${name} не найдено — проверь тест`);
    let depth = 0;
    for (let i = at; i < source.length; i += 1) {
        const ch = source[i];
        if (ch === '(' || ch === '[' || ch === '{') depth += 1;
        else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
        else if (ch === ';' && depth === 0) return source.slice(at, i + 1);
    }
    throw new Error(`не нашёл конец объявления ${name} — проверь тест`);
};

const NAMES = [
    'normalizeDepartmentCode',
    'aiQaHeadDepartmentCodesOf',
    'DISCIPLINE_DEPARTMENT_CODES',
    'DISCIPLINE_SUPER_ADMIN_ONLY',
    'DISCIPLINE_ROLLOUT_USER_IDS',
    'canAccessDisciplineSectionForUser',
    'isDisciplineOperatorUser',
];

const build = (flagOn) => {
    const code = NAMES.map(declarationOf).join('\n')
        .replace('const DISCIPLINE_SUPER_ADMIN_ONLY = true;', `const DISCIPLINE_SUPER_ADMIN_ONLY = ${flagOn};`);
    return new Function('deps', `
        const { isDepartmentHead, normalizeRole } = deps;
        ${code}
        return { canAccessDisciplineSectionForUser, isDisciplineOperatorUser, DISCIPLINE_SUPER_ADMIN_ONLY };
    `)({ isDepartmentHead, normalizeRole });
};

const SZOV = { department_id: 1, department_code: 'szov' };
const PEOPLE = [
    // [кто, пользователь, виден ли пункт при флаге, видна ли при снятом флаге, вид оператора]
    ['поимённо: 56, оператор СЗоВ', { id: 56, role: 'operator', ...SZOV }, true, true, true],
    ['поимённо, id строкой', { id: '56', role: 'operator', ...SZOV }, true, true, true],
    ['другой оператор СЗоВ', { id: 57, role: 'operator', ...SZOV }, false, true, true],
    ['56 в отделе вне раздела', { id: 56, role: 'operator', department_id: 4, department_code: 'remote_cc' }, false, false, false],
    ['56 с ролью админа', { id: 56, role: 'admin', ...SZOV }, false, false, false],
    ['супер-админ', { id: 1, role: 'super_admin' }, true, true, false],
    ['глава СЗоВ', { id: 100, role: 'admin', ...SZOV, headed_department_id: 1, headed_department_codes: ['szov'] },
        false, true, false],
    ['СВ СЗоВ', { id: 300, role: 'sv', ...SZOV }, false, true, false],
];

test('флаг в коде стоит — раздел выкладывается по шагам', () => {
    assert.equal(build(true).DISCIPLINE_SUPER_ADMIN_ONLY, true);
    assert.match(source, /const DISCIPLINE_SUPER_ADMIN_ONLY = true;/);
});

test('пока флаг стоит: супер-админ и поимённые — по обычным правилам', () => {
    const { canAccessDisciplineSectionForUser, isDisciplineOperatorUser } = build(true);
    for (const [who, user, visible, , operatorView] of PEOPLE) {
        assert.equal(canAccessDisciplineSectionForUser(user), visible, who);
        if (visible) assert.equal(isDisciplineOperatorUser(user), operatorView, who);
    }
});

test('флаг снят — поимённый список ничего не ломает: все по правилам ТЗ', () => {
    const { canAccessDisciplineSectionForUser, isDisciplineOperatorUser } = build(false);
    for (const [who, user, , visible, operatorView] of PEOPLE) {
        assert.equal(canAccessDisciplineSectionForUser(user), visible, who);
        if (visible) assert.equal(isDisciplineOperatorUser(user), operatorView, who);
    }
});
