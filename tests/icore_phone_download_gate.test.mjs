// Кому портал показывает «Скачать iCore Phone».
//
// Запрос владельца 07.10.2026: кнопку — главе отдела СЗоВ и всем сотрудникам,
// которых посадили на линию в разделе «Удаленный КЦ» (вкладка «Линии»). Прежний
// круг — отдел продаж, Тез КЦ, отдел удалённого КЦ и глобальные админы — остаётся.
//
// Исполняем НАСТОЯЩЕЕ объявление canDownloadIcorePhone из App.jsx над профилем в том
// виде, в каком его отдаёт сервер (_get_user_payload), а не переписываем условие в
// тест: потерянная в выражении строка иначе прошла бы незамеченной.
//
// Запуск: node --test tests/icore_phone_download_gate.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    headedDepartmentId, isAdminLikeRole, isDepartmentHead, isSupervisorRole, normalizeRole,
} from '../src/utils/roles.js';

const readLf = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const appSource = readLf('src/App.jsx');

/* Объявление `const NAME ...;` из App.jsx целиком. В отличие от соседних тестов
   пропускаем комментарии и строки: в этом объявлении комментарий содержит «;»,
   и наивный счёт скобок оборвал бы выражение на середине. */
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

/* Константы уровня модуля и флаги человека — настоящие объявления App.jsx. */
const MODULE_NAMES = ['ICORE_PHONE_DEPARTMENT_IDS', 'ICORE_PHONE_HEAD_DEPARTMENT_CODES',
    'DIAL_LIST_DEPARTMENT_CODES', 'normalizeDepartmentCode', 'aiQaHeadDepartmentCodesOf'];
const FLAG_NAMES = ['currentUserRole', 'isSuperAdmin', 'isDepartmentHeadUser', 'scopedDepartmentId',
    'isScopedDepartmentHead', 'isAdminLikeRole'];
const evaluateGate = new Function(
    'user', 'normalizeRole', 'isDepartmentHead', 'headedDepartmentId', 'isAdminLikeRoleFn', 'isSupervisorRole',
    `${[...MODULE_NAMES, ...FLAG_NAMES, 'canDownloadIcorePhone'].map(declarationOf).join('\n')}
     return { canDownloadIcorePhone, isAdminLikeRole, isScopedDepartmentHead };`,
);
const gate = (user) => evaluateGate(user, normalizeRole, isDepartmentHead, headedDepartmentId, isAdminLikeRole, isSupervisorRole);
const canDownload = (user) => gate(user).canDownloadIcorePhone;

/* Профили в том виде, в каком их отдаёт /api/auth/me (id отделов — как на проде). */
const SZOV = 1;
const profile = (overrides = {}) => ({
    id: 700, role: 'operator', department_id: SZOV, department_code: 'szov',
    headed_department_id: null, headed_department_ids: [], headed_department_code: null,
    headed_department_codes: [], dial_list_line_member: false, ...overrides,
});
// Глава СЗоВ, как заведена на проде: базовая роль admin плюс назначение главой.
const szovHead = (overrides = {}) => profile({
    id: 1, role: 'admin', headed_department_id: SZOV, headed_department_ids: [SZOV],
    headed_department_code: 'szov', headed_department_codes: ['szov'], ...overrides,
});

test('глава СЗоВ видит кнопку — при любой базовой роли', () => {
    for (const role of ['admin', 'sv', 'trainer', 'operator']) {
        assert.equal(canDownload(szovHead({ role })), true, role);
    }
    // Именно как глава: меню считает её руководителем отдела, а не админом —
    // поэтому до правки кнопки у неё и не было.
    const flags = gate(szovHead());
    assert.equal(flags.isAdminLikeRole, false);
    assert.equal(flags.isScopedDepartmentHead, true);
});

test('рядовым сотрудникам и супервайзерам СЗоВ кнопка не положена', () => {
    assert.equal(canDownload(profile()), false);
    assert.equal(canDownload(profile({ role: 'sv' })), false);
    assert.equal(canDownload(profile({ role: 'trainer' })), false);
});

test('глава другого отдела кнопку не получает', () => {
    for (const [id, code] of [[909, 'front_office'], [1499, 'hr'], [1041, 'marketing']]) {
        const head = profile({
            role: 'admin', department_id: id, department_code: code, headed_department_id: id,
            headed_department_ids: [id], headed_department_code: code, headed_department_codes: [code],
        });
        assert.equal(canDownload(head), false, code);
    }
});

test('сотрудник любого отдела, посаженный на линию, видит кнопку', () => {
    assert.equal(canDownload(profile({ dial_list_line_member: true })), true);
    assert.equal(canDownload(profile({ department_id: 909, department_code: 'front_office', dial_list_line_member: true })), true);
    // Человек без отдела на линии — тоже.
    assert.equal(canDownload(profile({ department_id: null, department_code: null, dial_list_line_member: true })), true);
    // Сняли с линии — сервер отдаёт false, кнопки снова нет.
    assert.equal(canDownload(profile({ department_id: 909, department_code: 'front_office', dial_list_line_member: false })), false);
    // Флаг — только булев true от сервера: поля нет или оно не булево — кнопки нет.
    for (const value of [undefined, null, 'true', 1, {}]) {
        assert.equal(canDownload(profile({ dial_list_line_member: value })), false, String(value));
    }
});

test('прежний круг остаётся: отдел продаж, Тез КЦ, удалённый КЦ и глобальные админы', () => {
    assert.equal(canDownload(profile({ department_id: 367, department_code: 'op' })), true);
    assert.equal(canDownload(profile({ department_id: 560, department_code: 'tez' })), true);
    assert.equal(canDownload(profile({ department_id: 1954, department_code: 'remote_cc' })), true);
    assert.equal(canDownload(profile({ role: 'admin' })), true);           // админ, не возглавляющий отдел
    assert.equal(canDownload(profile({ role: 'super_admin' })), true);
    assert.equal(canDownload(szovHead({ role: 'super_admin' })), true);
});

test('пункт меню стоит под этим условием и вне ролевых веток', () => {
    const item = appSource.indexOf('<span className="sidebar-text">Скачать iCore Phone</span>');
    assert.ok(item > 0);
    const before = appSource.slice(appSource.lastIndexOf('{canDownloadIcorePhone && (', item), item);
    assert.ok(before.startsWith('{canDownloadIcorePhone && (\n'));
    assert.ok(before.includes('<SidebarDeptScope section="download_icore_phone" activeCode={activeDeptCode}>'));
    assert.ok(before.includes('onClick={downloadIcorePhone}'));
    // Между условием и кнопкой нет ещё одного условия (ни роли, ни карты отдела).
    assert.equal(before.split('&& (').length - 1, 1);
});

test('селектор отдела у админа: кнопка видна при СЗоВ и удалённом КЦ', () => {
    const SidebarDeptScope = new Function(
        `${['SIDEBAR_SECTION_DEPARTMENTS', 'SidebarDeptScope'].map(declarationOf).join('\n')}\nreturn SidebarDeptScope;`,
    )();
    const node = { marker: 'пункт' };
    for (const code of ['op', 'tez', 'remote_cc', 'szov']) {
        assert.equal(SidebarDeptScope({ section: 'download_icore_phone', activeCode: code, children: node }), node, code);
    }
    for (const code of ['hr', 'accounting', 'front_office', 'marketing']) {
        assert.equal(SidebarDeptScope({ section: 'download_icore_phone', activeCode: code, children: node }), null, code);
    }
    assert.equal(SidebarDeptScope({ section: 'download_icore_phone', activeCode: null, children: node }), node);
});

test('список отделов-глав один и тот же на фронте и на сервере', () => {
    const front = /const ICORE_PHONE_HEAD_DEPARTMENT_CODES = new Set\(\[([^\]]*)\]\);/.exec(appSource);
    const back = /^ICORE_PHONE_HEAD_DEPARTMENT_CODES = \(([^)]*)\)/m.exec(readLf('bot_schedule2.py'));
    assert.ok(front && back, 'константа пропала с одной из сторон');
    const codes = (text) => [...text.matchAll(/'([a-z_]+)'/g)].map((m) => m[1]).sort();
    assert.deepEqual(codes(front[1]), codes(back[1]));
    assert.deepEqual(codes(front[1]), ['szov']);
});
