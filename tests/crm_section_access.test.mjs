import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { isDepartmentHead, normalizeRole } from '../src/utils/roles.js';

/**
 * Кто видит пункт «Обращения» в меню — по той же таблице людей, по которой
 * сервер решает, пускать ли в раздел (crm/access.py::can_open_section,
 * tests/test_crm_partner_departments.py). С 05.11.2026 в раздел входят и
 * отделы-гости — ОП и фронт-офисы, ради «Сотрудничества».
 *
 * Разойдись меню и сервер — человек увидит пункт, а раздел ответит 403, или
 * доступ у него будет, а пункта нет. Предикат берём из src/App.jsx как есть
 * (тот же приём, что в verifier_chats_access.test.mjs): файл монолитный и не
 * импортируется, а переписанный в тест предикат проверял бы копию вместо кода.
 */
const source = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
const table = JSON.parse(readFileSync(
    new URL('./fixtures/crm_section_people.json', import.meta.url), 'utf8'));

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
    'CRM_SECTION_DEPARTMENT_CODE',
    'CRM_PARTNER_DEPARTMENT_CODES',
    'CRM_DEPARTMENT_CODES',
    'isCrmSectionDepartmentHead',
    'canAccessCrmSectionForUser',
];

const { canAccessCrmSectionForUser } = new Function('deps', `
    const { isDepartmentHead, normalizeRole } = deps;
    ${NAMES.map(declarationOf).join('\n')}
    return { canAccessCrmSectionForUser };
`)({ isDepartmentHead, normalizeRole });

// Профиль — в том виде, в каком его отдаёт сервер (_build_user_payload).
const profileOf = (person, id) => {
    const heads = person.heads || [];
    const headedIds = heads.map((code) => table.departments[code]);
    return {
        id,
        role: person.role,
        department_id: person.department ? table.departments[person.department] : null,
        department_code: person.department,
        headed_department_id: headedIds[0] ?? null,
        headed_department_ids: headedIds,
        headed_department_code: heads[0] ?? null,
        headed_department_codes: heads,
    };
};

test('пункт «Обращения» видят ровно те, кого пускает сервер', () => {
    table.people.forEach((person, index) => {
        assert.equal(canAccessCrmSectionForUser(profileOf(person, 100 + index)), person.open,
            person.who);
    });
});

test('профиль из старого кэша (поля в camelCase) читается так же', () => {
    table.people.forEach((person, index) => {
        const profile = profileOf(person, 100 + index);
        const camel = {
            id: profile.id,
            role: profile.role,
            departmentCode: profile.department_code,
            headedDepartmentId: profile.headed_department_id,
            headedDepartmentCodes: profile.headed_department_codes,
        };
        assert.equal(canAccessCrmSectionForUser(camel), person.open, person.who);
    });
});

test('отделы-гости в таблице есть — сверка касается самой правки', () => {
    const open = new Set(table.people.filter((p) => p.open).map((p) => p.department));
    assert.ok(open.has('op'));
    assert.ok(open.has('front_office'));
    assert.ok(table.people.some((p) => p.open && p.heads.includes('front_office')));
});
