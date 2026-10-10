// Кому портал открывает раздел «Удаленный КЦ» (ключ dial_list).
//
// Решение владельца 10.10.2026: «открыть раздел удаленный КЦ всем супервайзерам СЗоВ
// с полным доступом». Что внутри раздела можно делать, решает сервер (СВ СЗоВ он
// отдаёт то же, что главе СЗоВ, — tests/test_dial_list_szov_supervisors.py); портал
// решает пункт меню, страж маршрута и экран — все по флагу canAccessDialListSection.
//
// Исполняем НАСТОЯЩЕЕ объявление флага из App.jsx над профилем в том виде, в каком
// его отдаёт сервер (_get_user_payload), а не переписываем условие в тест.
//
// Запуск: node --test tests/dial_list_section_access.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    headedDepartmentId, isAdminLikeRole, isDepartmentHead, isSupervisorRole, normalizeRole,
} from '../src/utils/roles.js';

const readLf = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const appSource = readLf('src/App.jsx');

/* Объявление `const NAME ...;` из App.jsx целиком: комментарии и строки пропускаем,
   скобки считаем — как в tests/icore_phone_download_gate.test.mjs. */
const declarationOf = (name) => {
    const at = appSource.indexOf(`const ${name} `);
    assert.ok(at >= 0, `объявление ${name} не найдено — проверь тест`);
    let depth = 0;
    for (let i = at; i < appSource.length; i += 1) {
        const ch = appSource[i];
        const next = appSource[i + 1];
        if (ch === '/' && next === '/') { i = appSource.indexOf('\n', i); continue; }
        if (ch === '/' && next === '*') { i = appSource.indexOf('*/', i) + 1; continue; }
        if (ch === "'" || ch === '"' || ch === '`') {
            let j = i + 1;
            while (j < appSource.length && appSource[j] !== ch) j += appSource[j] === '\\' ? 2 : 1;
            i = j;
            continue;
        }
        if (ch === '(' || ch === '[' || ch === '{') depth += 1;
        else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
        else if (ch === ';' && depth === 0) return appSource.slice(at, i + 1);
    }
    throw new Error(`не нашёл конец объявления ${name} — проверь тест`);
};

const MODULE_NAMES = ['DIAL_LIST_DEPARTMENT_CODES', 'DIAL_LIST_PILOT_LOGINS', 'DIAL_LIST_OVERSEER_DEPARTMENT_CODES',
    'DIAL_LIST_FULL_ACCESS_SUPERVISOR_DEPARTMENT_CODES', 'dialListPilotAllows', 'normalizeDepartmentCode',
    'aiQaHeadDepartmentCodesOf'];
const FLAG_NAMES = ['currentUserRole', 'isSuperAdmin', 'isDepartmentHeadUser', 'scopedDepartmentId',
    'isScopedDepartmentHead', 'isAdminLikeRole', 'isDepartmentManager'];
const evaluateFlags = new Function(
    'user', 'normalizeRole', 'isDepartmentHead', 'headedDepartmentId', 'isAdminLikeRoleFn', 'isSupervisorRole',
    `${[...MODULE_NAMES, ...FLAG_NAMES, 'canAccessDialListSection'].map(declarationOf).join('\n')}
     return { canAccessDialListSection, isAdminLikeRole, isDepartmentManager };`,
);
const flags = (user) => evaluateFlags(user, normalizeRole, isDepartmentHead, headedDepartmentId, isAdminLikeRole, isSupervisorRole);
const canOpen = (user) => flags(user).canAccessDialListSection;

/* Профили в том виде, в каком их отдаёт /api/auth/me (id отделов — как на проде). */
const SZOV = 1;
const profile = (overrides = {}) => ({
    id: 700, role: 'sv', login: 'sv_szov', department_id: SZOV, department_code: 'szov',
    headed_department_id: null, headed_department_ids: [], headed_department_code: null,
    headed_department_codes: [], ...overrides,
});
const head = (id, code, overrides = {}) => profile({
    role: 'admin', department_id: id, department_code: code, headed_department_id: id,
    headed_department_ids: [id], headed_department_code: code, headed_department_codes: [code], ...overrides,
});

test('супервайзер СЗоВ видит раздел — и в ветке меню руководителей, а не админов', () => {
    for (const role of ['sv', 'supervisor', ' SV ']) {
        assert.equal(canOpen(profile({ role })), true, role);
    }
    // Код в карточке отдела бывает записан как угодно.
    assert.equal(canOpen(profile({ department_code: ' SZOV ' })), true);
    const f = flags(profile());
    assert.equal(f.isAdminLikeRole, false);
    assert.equal(f.isDepartmentManager, true);
});

test('СВ других отделов, рядовые и тренеры СЗоВ раздела не получают', () => {
    for (const [id, code] of [[367, 'op'], [560, 'tez'], [1954, 'remote_cc'], [909, 'front_office']]) {
        assert.equal(canOpen(profile({ department_id: id, department_code: code })), false, code);
    }
    for (const role of ['operator', 'trainee', 'trainer']) {
        assert.equal(canOpen(profile({ role })), false, role);
    }
    // Нет отдела в профиле — нет и раздела.
    assert.equal(canOpen(profile({ department_id: null, department_code: null })), false);
});

test('прежний круг не изменился', () => {
    assert.equal(canOpen(profile({ role: 'admin', department_id: null, department_code: null })), true);
    assert.equal(canOpen(profile({ role: 'super_admin' })), true);
    assert.equal(canOpen(head(SZOV, 'szov')), true);                 // глава СЗоВ
    assert.equal(canOpen(head(1954, 'remote_cc')), true);            // глава удалённого КЦ
    assert.equal(canOpen(head(367, 'op')), false);                   // глава другого отдела
    assert.equal(canOpen(head(367, 'op', { role: 'operator' })), false);
});

test('пункт меню, страж маршрута и экран держатся на этом флаге', () => {
    const nav = "handleSidebarViewNavigation(e, 'dial_list')";
    const items = [...appSource.matchAll(/handleSidebarViewNavigation\(e, 'dial_list'\)/g)].map((m) => m.index);
    assert.equal(items.length, 2, `${nav}: ждём пункт у админов и у руководителей`);
    // Второй пункт — в ветке руководителей (СВ и главы), её СВ СЗоВ и видит.
    const branches = ['{isAdminLikeRole && (', '{isDepartmentManager && !isAdminLikeRole && ('];
    const owner = (at) => branches
        .map((opener) => ({ opener, index: appSource.lastIndexOf(opener, at) }))
        .sort((a, b) => b.index - a.index)[0].opener;
    assert.equal(owner(items[1]), '{isDepartmentManager && !isAdminLikeRole && (');
    for (const at of items) {
        const gate = appSource.lastIndexOf('{canAccessDialListSection && (', at);
        assert.ok(gate > 0 && at - gate < 600, 'над пунктом нет своего условия');
        // Между условием и кнопкой нет ещё одного условия.
        assert.equal(appSource.slice(gate, at).split('&& (').length - 1, 1);
    }
    assert.ok(appSource.includes("if (view === 'dial_list' && canAccessDialListSection) return;"));
    assert.ok(appSource.includes('{( view === "dial_list" && canAccessDialListSection && ('));
});
