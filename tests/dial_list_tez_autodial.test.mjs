// Раздел «Автообзвон Тез» (15.10.2026): чистая логика сайта (src/components/dial_list/tezAutodial.js),
// итоги с датой (outcomeFormat.js) и кому портал открывает раздел (флаг canAccessTezAutodialSection
// из App.jsx — исполняем НАСТОЯЩЕЕ объявление, как tests/dial_list_section_access.test.mjs).
//
// Запуск: node --test tests/dial_list_tez_autodial.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    LIMITS, PROFILE_REMOTE_CC, PROFILE_TEZ, attemptDueText, attemptModeText, departmentsOfProfile, formatMinutes,
    leadDue, mirrorSummary,
    normalizeProfile, previewTemplate, retryPlanText, scheduleFromInputs, serializeOffers, templateBlockReason,
    tezSettingsPayload, tripLabel, validateOffers, validateTezSettings, validateWaAllowed,
} from '../src/components/dial_list/tezAutodial.js';
import { outcomesSnapshot, serializeOutcomes } from '../src/components/dial_list/outcomeFormat.js';
import {
    headedDepartmentId, isAdminLikeRole, isDepartmentHead, isSupervisorRole, normalizeRole,
} from '../src/utils/roles.js';

test('профиль: пустое и незнакомое — удалённый КЦ; список отделов режется по профилю', () => {
    assert.equal(normalizeProfile('TEZ '), PROFILE_TEZ);
    for (const value of [undefined, null, '', 'remote_cc', 'other']) assert.equal(normalizeProfile(value), PROFILE_REMOTE_CC);
    const list = [{ department_id: 1954 }, { department_id: 560, profile: 'tez' }, { department_id: 7, profile: 'remote_cc' }];
    assert.deepEqual(departmentsOfProfile(list, 'tez').map((d) => d.department_id), [560]);
    assert.deepEqual(departmentsOfProfile(list, 'remote_cc').map((d) => d.department_id), [1954, 7]);
});

test('расписание недозвонов словами и пределы настроек — как у сервера', () => {
    assert.deepEqual([45, 60, 90, 1440, 2880].map(formatMinutes), ['45 мин', '1 ч', '1 ч 30 мин', '24 ч', '2 сут']);
    assert.deepEqual(scheduleFromInputs(['60', ' ', '180', '1440']), [60, 180, 1440]);
    assert.equal(retryPlanText([60, 180, 1440], 4), '2-я попытка — через 1 ч, 3-я — через 3 ч, 4-я — через 24 ч');
    assert.equal(retryPlanText([60], 3), '2-я попытка — через 1 ч, 3-я — через 1 ч');
    assert.equal(retryPlanText([], 3), '');
    // Пределы — те же, что у save_department_settings (WRAPUP_MIN_SEC 10, CALL_INTERVAL_MAX_SEC 600…).
    assert.deepEqual(LIMITS.wrapup_sec, [10, 1800]);
    assert.deepEqual(LIMITS.call_interval_sec, [0, 600]);
    const form = {
        auto_dial: true, portion_size: '20', max_attempts: '3', retry_after_hours: '0', call_interval_sec: '10',
        wrapup_sec: '0', retry_schedule: ['60', '180'], callback_own_minutes: '120', followup_after_min: '60',
        caller_id_for_employee: '', active_period: '',
    };
    const payload = tezSettingsPayload(form);
    // Режим отдела целиком у Тез не включается — ключа enabled в запросе нет вовсе.
    assert.equal(Object.prototype.hasOwnProperty.call(payload, 'enabled'), false);
    assert.deepEqual(payload.retry_schedule_min, [60, 180]);
    assert.equal(validateTezSettings(payload), '');
    assert.match(validateTezSettings({ ...payload, wrapup_sec: 5 }), /Постобработка/);
    assert.match(validateTezSettings({ ...payload, retry_schedule_min: [0] }), /Интервал недозвона/);
});

test('сроки строки журнала и метка поездок', () => {
    const now = new Date(2026, 9, 15, 12, 0);
    const past = new Date(2026, 9, 15, 11, 0).toISOString();
    const later = new Date(2026, 9, 15, 18, 30).toISOString();
    assert.deepEqual(leadDue({ stage: 'callback', owner_call_at: later }, now), { text: 'перезвон сегодня, 18:30', overdue: false });
    assert.equal(leadDue({ stage: 'callback', owner_call_at: past }, now).overdue, true);
    assert.deepEqual(leadDue({ stage: 'followup', owner_call_at: past, promised_online_at: past }, now),
        { text: 'дожать · обещал сегодня, 11:00', overdue: true });
    assert.equal(leadDue({ stage: 'queue' }, now), null);
    // Метку сервер присылает готовой; без неё — те же правила.
    assert.equal(tripLabel({ label: 'Работал 01.10' }), 'Работал 01.10');
    assert.equal(tripLabel({ checked_at: past }, now), 'Заказов не было');
    assert.equal(tripLabel({ checked_at: past, last_order_before_at: new Date(2026, 7, 1).toISOString() }, now),
        'Отток · последний заказ 01.08.2026');
    assert.equal(tripLabel({}), '');
});

test('звонок в карточке водителя: срок словами итога и кто начал звонок', () => {
    const now = new Date(2026, 9, 15, 12, 0);
    const at = new Date(2026, 9, 16, 18, 0).toISOString();
    // Форма попытки — как у lead_card: due_kind рядом с due_at, итог — без него.
    assert.equal(attemptDueText({ due_at: at, due_kind: 'online', outcome: { id: 'o', name: 'Согласен' } }, now),
        'выйдет на линию завтра, 18:00');
    assert.equal(attemptDueText({ due_at: at, due_kind: 'callback' }, now), 'перезвонить завтра, 18:00');
    assert.equal(attemptDueText({ due_at: null, due_kind: '' }, now), '');
    assert.equal(attemptModeText({ auto: true, operator_status: 'active' }), 'автообзвон');
    assert.equal(attemptModeText({ auto: false, operator_status: 'outbound' }), 'вручную, «Исход»');
    assert.equal(attemptModeText({ auto: false, operator_status: '' }), '');
});

test('офферы и шаблоны WhatsApp проверяются до запроса', () => {
    const offers = serializeOffers([{ id: 'new-1', title: ' Бонус ', description: '' }, { id: 'o-2', title: 'Комиссия 0%' }]);
    assert.deepEqual(offers.map((o) => [o.id, o.title, o.position]), [[undefined, 'Бонус', 1], ['o-2', 'Комиссия 0%', 2]]);
    assert.equal(validateOffers(offers), '');
    assert.match(validateOffers([...offers, { title: 'бонус' }]), /повторяется/);
    assert.equal(templateBlockReason({ status: 'APPROVED', type: 'TEXT', supported: true }), '');
    assert.equal(templateBlockReason({ status: 'PENDING', type: 'TEXT' }), 'не утверждён в WhatsApp');
    assert.equal(previewTemplate('Здравствуйте, {{1}}! Я {{2}}', ['{first_name}', '{operator}']),
        'Здравствуйте, Айбек! Я Алия');
    assert.match(validateWaAllowed([{ id: 't', title: 'Бонус', text: 'Привет, {{1}}', params: [''] }]), /заполните параметр/);
    assert.match(validateWaAllowed([{ id: 't', title: 'Бонус', text: 'Привет, {{1}}', params: ['{phone}'] }]),
        /подстановки \{phone\} нет/);
    assert.equal(mirrorSummary({ error: 'нет связи' }).ok, false);
    assert.equal(mirrorSummary({ rows_new: 2, rows_duplicate: 1 }).text, 'В «Базе лидов ОП TEZ»: новых 2, повторов 1');
    assert.equal(mirrorSummary(null), null);
});

test('итоги: дата уходит только у Тез, у удалённого КЦ запрос прежний', () => {
    const items = [{ id: 'o-1', name: 'Перезвонить', color: '#FF9F0A', requeue: false, is_active: true, due_kind: 'callback', subtypes: [] },
        { id: 'o-2', name: 'Отказ', color: '#FF3B30', requeue: false, is_active: true, due_kind: 'нечто', subtypes: [] }];
    const plain = serializeOutcomes(items);
    assert.equal(plain.some((o) => Object.prototype.hasOwnProperty.call(o, 'due_kind')), false);
    assert.equal(plain[0].requeue, false);
    const tez = serializeOutcomes(items, { dueKinds: true });
    // Итог с датой всегда «перезвонить»; незнакомое — «без даты», и '' уходит явно (снятая дата).
    assert.deepEqual(tez.map((o) => [o.due_kind, o.requeue]), [['callback', true], ['', false]]);
    assert.notEqual(outcomesSnapshot(items), outcomesSnapshot(items, { dueKinds: true }));
});

/* ─── кому раздел открыт ──────────────────────────────────────────────── */

const appSource = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8').split('\r\n').join('\n');

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

const MODULE_NAMES = ['DIAL_LIST_PILOT_LOGINS', 'TEZ_AUTODIAL_DEPARTMENT_CODES', 'SIP_SETTINGS_TEZ_DEPARTMENT_ID',
    'SIP_SETTINGS_BINOTEL_DEPARTMENT_CODES', 'dialListPilotAllows', 'normalizeDepartmentCode', 'aiQaHeadDepartmentCodesOf',
    'isSipSettingsTezDepartmentHead'];
const FLAG_NAMES = ['currentUserRole', 'isSuperAdmin', 'isDepartmentHeadUser', 'scopedDepartmentId',
    'isScopedDepartmentHead', 'isAdminLikeRole'];
const evaluate = new Function(
    'user', 'normalizeRole', 'isDepartmentHead', 'headedDepartmentId', 'isAdminLikeRoleFn', 'isSupervisorRole',
    `${[...MODULE_NAMES, ...FLAG_NAMES, 'canAccessTezAutodialSection'].map(declarationOf).join('\n')}
     return canAccessTezAutodialSection;`,
);
const canOpen = (user) => evaluate(user, normalizeRole, isDepartmentHead, headedDepartmentId, isAdminLikeRole, isSupervisorRole);

const profile = (overrides = {}) => ({
    id: 3, role: 'sv', login: 'sv_tez', department_id: 560, department_code: 'tez',
    headed_department_id: null, headed_department_ids: [], headed_department_code: null,
    headed_department_codes: [], ...overrides,
});
const head = (id, code) => profile({
    role: 'admin', department_id: id, department_code: code, headed_department_id: id,
    headed_department_ids: [id], headed_department_code: code, headed_department_codes: [code],
});

test('раздел видят админы, глава и супервайзеры Тез КЦ', () => {
    assert.equal(canOpen(profile()), true);                                   // СВ Тез
    assert.equal(canOpen(profile({ department_code: null })), true);          // код не проставлен — по id
    assert.equal(canOpen(profile({ role: 'admin', department_id: null, department_code: null })), true);
    assert.equal(canOpen(head(560, 'tez')), true);                            // глава Тез КЦ
});

test('прочим раздел не открыт', () => {
    for (const [id, code] of [[1, 'szov'], [367, 'op'], [1954, 'remote_cc']]) {
        assert.equal(canOpen(profile({ department_id: id, department_code: code })), false, code);
    }
    for (const role of ['operator', 'trainee', 'trainer']) assert.equal(canOpen(profile({ role })), false, role);
    assert.equal(canOpen(head(1954, 'remote_cc')), false);
    assert.equal(canOpen(head(367, 'op')), false);
});

test('пункт меню в обеих ветках, страж маршрута и экран держатся на этом флаге', () => {
    const items = [...appSource.matchAll(/handleSidebarViewNavigation\(e, 'tez_autodial'\)/g)].map((m) => m.index);
    assert.equal(items.length, 2, 'ждём пункт у админов и у руководителей');
    for (const at of items) {
        const gate = appSource.lastIndexOf('{canAccessTezAutodialSection && (', at);
        assert.ok(gate > 0 && at - gate < 700, 'над пунктом нет своего условия');
    }
    assert.ok(appSource.includes("if (view === 'tez_autodial' && canAccessTezAutodialSection) return;"));
    assert.ok(appSource.includes('{( view === "tez_autodial" && canAccessTezAutodialSection && ('));
    const screen = appSource.slice(appSource.indexOf('{( view === "tez_autodial"'));
    assert.ok(screen.slice(0, 900).includes('profile="tez"'), 'экран раздела — с профилем tez');
    assert.ok(appSource.includes("tez_autodial: ['tez'],"));
});
