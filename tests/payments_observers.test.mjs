/*
 * Наблюдатели заявки «Оплаты счетов» в интерфейсе — просьба владельца 15.10.2026:
 * «ты уходишь на отпуск и добавляешь одного или нескольких наблюдателей, чтобы они
 * следили за прогрессом; инициатор заявки сам может добавить их в начале и в
 * прогрессе заявки».
 *
 * 1. Подписи (paymentsMeta.js): свёрнутый раздел, строка выбранных, запись истории.
 * 2. Окно заявки С ДАННЫМИ: раздел «Наблюдатели» — кому он виден, что в нём может
 *    инициатор и чего не может наблюдатель. Серверный рендер эффектов не запускает,
 *    поэтому ответ сервера и раскрытые разделы подкладываются в начальное состояние
 *    окна при сборке (подмена — только начальные значения `data` и `folds`).
 * 3. Форма заявки: поле «Наблюдатели» за чипом в «Дополнительно», у доработки —
 *    раскрыто с теми, кто уже наблюдает.
 *
 * Запуск: node --test tests/payments_observers.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
    CARD_FOLDS, HINTS, MAX_OBSERVERS, STATE_FILTERS, describeEvent, observersClosedLine, observersPatch, observersSummary,
    peopleParts,
} from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const esbuild = require('esbuild');
const ROOT = fileURLToPath(new URL('../', import.meta.url));

/* ── 1. Подписи ────────────────────────────────────────────────────────────── */

test('свёрнутый раздел: кто наблюдает — одной строкой, сам смотрящий — «вы» и первым', () => {
    const people = [{ user_id: 5, name: 'Иванова Айгерим' }, { user_id: 7, name: 'Петров Пётр' }, { user_id: 9, name: 'Ким Ольга' }];
    assert.equal(observersSummary([]), 'не добавлены');
    assert.equal(observersSummary(null), 'не добавлены');
    assert.equal(observersSummary(people.slice(0, 1)), 'Иванова Айгерим');
    assert.equal(observersSummary(people), 'Иванова Айгерим и ещё 2');
    assert.equal(observersSummary(people, 9), 'вы и ещё 2');
    assert.equal(observersSummary([people[1]], 7), 'вы');
    assert.equal(observersSummary(people, 1), 'Иванова Айгерим и ещё 2', 'не наблюдатель — имена, а не «вы»');
});

test('кнопка выбора нескольких людей: первое имя и «и ещё N» — счётчик виден уже при двух', () => {
    const users = [{ id: 5, name: 'Иванова Айгерим' }, { id: 7, name: 'Петров Пётр' }, { id: 9, name: 'Ким Ольга' }];
    // Номера приходят из списка выбора строками. Разбор 15.10.2026: «А, Б» на телефоне обрезалось
    // на первом имени, и второй человек пропадал из виду вместе со счётчиком.
    assert.deepEqual(peopleParts(['5'], users), { first: 'Иванова Айгерим', rest: 0, total: 1 });
    assert.deepEqual(peopleParts(['5', '7'], users), { first: 'Иванова Айгерим', rest: 1, total: 2 });
    assert.deepEqual(peopleParts(['5', '7', '9'], users), { first: 'Иванова Айгерим', rest: 2, total: 3 });
    // Нет в списке сотрудников — не печатается, но считается.
    assert.deepEqual(peopleParts([404, 7], users), { first: 'Петров Пётр', rest: 1, total: 2 });
    assert.deepEqual(peopleParts([404], users), { first: '', rest: 1, total: 1 });
    assert.deepEqual(peopleParts(['5', 5], users), { first: 'Иванова Айгерим', rest: 0, total: 1 }, 'один человек дважды — один');
    assert.deepEqual(peopleParts([], users), { first: '', rest: 0, total: 0 });
});

test('форма шлёт наблюдателей разницей с открытием, а не списком целиком', () => {
    // Разбор 15.10.2026: список целиком снимал того, кого админ добавил в окне заявки,
    // пока форма доработки была открыта. Разница его не касается.
    assert.deepEqual(observersPatch([], []), {}, 'новая заявка без выбора — ничего');
    assert.deepEqual(observersPatch(['5', '7'], []), { observers_add: [5, 7] }, 'новая заявка — выбранные');
    assert.deepEqual(observersPatch(['5', '7'], ['7', '5']), {}, 'доработка без изменений — ничего');
    assert.deepEqual(observersPatch(['5', '9'], ['5', '7']), { observers_add: [9], observers_remove: [7] });
    assert.deepEqual(observersPatch([], ['5']), { observers_remove: [5] }, 'поле убрали — сняли бывших при открытии');
    assert.deepEqual(observersPatch(['5', 5], [5]), {}, 'номер строкой и числом — один человек');
    assert.deepEqual(observersPatch(null, null), {});
});

test('кому раздел не открыт — короткой строкой: до двух имён, дальше «и ещё N»', () => {
    assert.equal(observersClosedLine([], 3), '');
    assert.equal(observersClosedLine(['Иванова Айгерим'], 1), 'Раздел не открыт: Иванова Айгерим — заявку пока не увидит.');
    assert.equal(observersClosedLine(['А', 'Б'], 2), 'Раздел не открыт: А, Б — заявку пока не увидят.');
    assert.equal(observersClosedLine(['А', 'Б', 'В'], 5), 'Раздел не открыт: А, Б и ещё 1 — заявку пока не увидят.');
    // Десять длинных ФИО подряд на телефоне занимали восемь строк (разбор 15.10.2026).
    assert.equal(observersClosedLine(['А', 'Б', 'В'], 3), 'Никому из наблюдателей раздел пока не открыт — заявку они не увидят.');
});

test('история: кого добавили и кого сняли — одной записью', () => {
    const event = (added, removed) => ({ kind: 'observers', payload: { added, removed } });
    const a = { id: 1, name: 'Иванова Айгерим' };
    const b = { id: 2, name: 'Петров Пётр' };
    assert.equal(describeEvent(event([a], [])), 'Добавлен наблюдатель: Иванова Айгерим');
    assert.equal(describeEvent(event([a, b], [])), 'Добавлены наблюдатели: Иванова Айгерим, Петров Пётр');
    assert.equal(describeEvent(event([], [b])), 'Снят наблюдатель: Петров Пётр');
    assert.equal(describeEvent(event([], [a, b])), 'Сняты наблюдатели: Иванова Айгерим, Петров Пётр');
    assert.equal(describeEvent(event([a], [b])), 'Наблюдатели: добавлен — Иванова Айгерим; снят — Петров Пётр');
    assert.equal(describeEvent({ kind: 'observers', payload: {} }), 'Изменены наблюдатели');
});

test('раздел окна, срез реестра, подсказка и предел — на своих местах', () => {
    assert.deepEqual(CARD_FOLDS.find((fold) => fold.key === 'observers'), { key: 'observers', title: 'Наблюдатели' });
    assert.equal(STATE_FILTERS.find((item) => item.key === 'watching')?.label, 'Наблюдаю');
    assert.match(HINTS.observers.intro, /следить за ходом заявки/);
    assert.match(HINTS.observers.outro, /Действовать по заявке он не может/);
    // Тот же предел, что на сервере (workflow.MAX_OBSERVERS) — сервер отдаёт его и в meta.
    const workflow = readFileSync(new URL('../payments/workflow.py', import.meta.url), 'utf8');
    assert.match(workflow, new RegExp(`^MAX_OBSERVERS = ${MAX_OBSERVERS}$`, 'm'));
});

test('реестр: плитка «Наблюдаю» — только когда наблюдаемые заявки есть', () => {
    const view = readFileSync(new URL('../src/components/payments/PaymentsView.jsx', import.meta.url), 'utf8');
    assert.ok(view.includes(".filter((item) => ['all', 'mine', 'open'].includes(item.key) || counters[item.counter] || state === item.key)"));
});

/* ── 2. Окно заявки с данными ──────────────────────────────────────────────── */

const DATA_SEED = 'const [data, setData] = useState(null);';
const FOLDS_SEED = 'const [folds, setFolds] = useState({});';

const bundled = {};
async function build(file, seeds) {
    if (bundled[file]) return bundled[file];
    const result = await esbuild.build({
        stdin: {
            contents: `
                import React from 'react';
                import { renderToStaticMarkup } from 'react-dom/server';
                import View from './src/components/payments/${file}';
                export const render = (props) => renderToStaticMarkup(React.createElement(View, props));
            `,
            resolveDir: ROOT, loader: 'jsx',
        },
        bundle: true, write: false, format: 'cjs', platform: 'node', target: 'node18', jsx: 'transform',
        loader: { '.jsx': 'jsx', '.js': 'jsx', '.css': 'empty' },
        logLevel: 'silent',
        plugins: [{
            name: 'seed-state',
            setup(b) {
                b.onResolve({ filter: /^axios$/ }, () => ({ path: 'axios', namespace: 'stub' }));
                b.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({
                    contents: 'const never = () => new Promise(() => {}); export default { get: never, post: never, patch: never, delete: never };',
                }));
                b.onLoad({ filter: new RegExp(`${file.replace('.', '\\.')}$`) }, (args) => {
                    let source = readFileSync(args.path, 'utf8');
                    for (const [from, to] of seeds) {
                        assert.ok(source.includes(from), `в ${file} изменилось «${from}» — поправьте подмену`);
                        source = source.replace(from, to);
                    }
                    return { contents: source, loader: 'jsx' };
                });
            },
        }],
    });
    const module = { exports: {} };
    new Function('module', 'exports', 'require', result.outputFiles[0].text)(module, module.exports, require);
    bundled[file] = module.exports.render;
    return bundled[file];
}

const USERS = [
    { id: 10, name: 'Ерсултан Айгерим', department_name: 'Офис', section_open: true },
    { id: 20, name: 'Хайрихан Шерзад', department_name: 'IT-отдел', section_open: true },
    { id: 21, name: 'Продажина Пелагея', department_name: 'Отдел продаж', section_open: false },
    { id: 22, name: 'Бывшев Борис', section_open: false },
];

const answer = ({ observers = [], permissions = {}, photos = {}, request = {} } = {}) => ({
    request: {
        id: 576, status: 'active', stage: 'approval', request_kind: 'purchase', expense_name: 'Бумага А4', amount: 25000,
        initiator_id: 10, initiator_name: 'Ерсултан Айгерим', submitted_at: '2026-10-15T10:00:00', ...request,
    },
    items: [], offers: [], subtasks: [], lifecycle: [], attachments: [], events: [], history: [], duplicates: [],
    assets: [], clarify_reasons: [], submit_problems: [], closing: [], contract_check: { applies: false, ok: true },
    permissions, observers, photos,
});

const OBSERVERS = [
    { user_id: 21, name: 'Продажина Пелагея', department_name: 'Отдел продаж', fired: false, section_open: false },
    { user_id: 20, name: 'Хайрихан Шерзад', department_name: 'IT-отдел', fired: false, section_open: true },
    { user_id: 22, name: 'Бывшев Борис', department_name: null, fired: true, section_open: false },
];

async function card(data, { me = 10, open = false, meta = {} } = {}) {
    const render = await build('PaymentRequestCard.jsx', [
        [DATA_SEED, 'const [data, setData] = useState(globalThis.__CARD_DATA__ ?? null);'],
        [FOLDS_SEED, 'const [folds, setFolds] = useState(globalThis.__CARD_FOLDS__ ?? {});'],
    ]);
    globalThis.__CARD_DATA__ = data;
    globalThis.__CARD_FOLDS__ = open ? { observers: true } : {};
    try {
        return render({
            apiBaseUrl: 'http://api.test', headers: () => ({}), dictionaries: {}, users: USERS, me: { id: me }, meta,
            showToast: () => {}, onClose: () => {}, open: true, requestId: data.request.id,
        });
    } finally {
        delete globalThis.__CARD_DATA__;
        delete globalThis.__CARD_FOLDS__;
    }
}

const foldOf = (html) => {
    const start = html.indexOf('data-fold="observers"');
    if (start < 0) return '';
    const end = html.indexOf('data-fold="', start + 10);
    return html.slice(start, end < 0 ? undefined : end);
};
const text = (chunk) => chunk.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

test('окно: свёрнутый раздел говорит, кто наблюдает', async () => {
    const html = await card(answer({ observers: OBSERVERS, permissions: { can_manage_observers: true } }));
    const fold = foldOf(html);
    assert.ok(fold, 'раздел «Наблюдатели» есть');
    assert.match(text(fold), /^.*Наблюдатели Продажина Пелагея и ещё 2/);
    assert.doesNotMatch(fold, /data-observer=/, 'свёрнутый — без списка');
    // Порядок разделов: ход заявки → наблюдатели → … → история.
    const order = [...html.matchAll(/data-fold="([a-z]+)"/g)].map((match) => match[1]);
    assert.deepEqual(order.filter((key) => key !== 'files'), ['route', 'observers', 'history']);
});

test('окно инициатора: список с «снять», выбор и «Добавить»; пометки доступа и увольнения', async () => {
    const html = await card(answer({ observers: OBSERVERS, permissions: { can_manage_observers: true },
        photos: { 20: 'https://photo/20.jpg' } }), { open: true });
    const fold = foldOf(html);
    assert.equal((fold.match(/data-observer="/g) || []).length, 3);
    assert.equal((fold.match(/aria-label="Снять наблюдателя: /g) || []).length, 3);
    assert.match(fold, /aria-label="Снять наблюдателя: Продажина Пелагея"/);
    assert.match(fold, /data-observer-add/);
    assert.match(fold, /Кого добавить наблюдателем/);
    assert.match(text(fold), /Добавить/);
    // Кому раздел не открыт — ОДНОЙ строкой под списком, по именам (уволенного в ней нет:
    // у него своя пометка), а не повтором под каждым человеком.
    assert.equal((fold.match(/Раздел не открыт/g) || []).length, 1);
    assert.match(text(fold), /Раздел не открыт: Продажина Пелагея — заявку пока не увидит\./);
    assert.match(fold, /Больше не работает в компании/);
    // Фото — из списка ответа по номеру, у остальных — инициалы.
    assert.match(fold, /src="https:\/\/photo\/20\.jpg"/);
    assert.match(fold, /data-face="initials"/);
    // Пустой выбор — «Добавить» неактивна.
    assert.match(fold, /<button[^>]*disabled=""[^>]*>[^]*?Добавить/);
});

test('окно наблюдателя: тот же список — без «снять» и без выбора, сам он — «вы»', async () => {
    const html = await card(answer({ observers: OBSERVERS, permissions: { can_manage_observers: false } }), { me: 20, open: true });
    const fold = foldOf(html);
    assert.equal((fold.match(/data-observer="/g) || []).length, 3);
    assert.doesNotMatch(fold, /Снять наблюдателя/);
    assert.doesNotMatch(fold, /data-observer-add/);
    assert.match(text(fold), /Хайрихан Шерзад · вы/);
    assert.match(text(fold), /Наблюдатели вы и ещё 2/);
    assert.doesNotMatch(fold, /Раздел не открыт/, 'исправить он ничего не может — и не видит этой строки');
});

test('окно: несколько без доступа — одна строка во множественном числе', async () => {
    const closed = [21, 23].map((id) => ({ user_id: id, name: `Закрытов ${id}`, fired: false, section_open: false }));
    const fold = foldOf(await card(answer({ observers: closed, permissions: { can_manage_observers: true } }), { open: true }));
    assert.match(text(fold), /Раздел не открыт: Закрытов 21, Закрытов 23 — заявку пока не увидят\./);
});

test('окно: без наблюдателей раздел есть только у того, кто может их добавить', async () => {
    assert.equal(foldOf(await card(answer({ permissions: { can_manage_observers: false } }))), '',
        'у остальных пустого раздела нет — это шум');
    const html = await card(answer({ permissions: { can_manage_observers: true } }), { open: true });
    const fold = foldOf(html);
    assert.match(text(fold), /Наблюдатели не добавлены/);
    assert.match(fold, /Наблюдатель видит заявку и её ход/);
    assert.match(fold, /data-observer-add/);
});

test('окно: дошли до предела — выбора нет, есть пояснение', async () => {
    const many = Array.from({ length: 3 }, (_, i) => ({ user_id: 100 + i, name: `Н${i}`, fired: false, section_open: true }));
    const html = await card(answer({ observers: many, permissions: { can_manage_observers: true } }),
        { open: true, meta: { max_observers: 3 } });
    const fold = foldOf(html);
    assert.doesNotMatch(fold, /data-observer-add/);
    assert.match(text(fold), /Наблюдателей у заявки — не больше 3\./);
});

/* ── 3. Форма заявки ───────────────────────────────────────────────────────── */

async function form(props) {
    const render = await build('PaymentRequestForm.jsx', []);
    return render({
        open: true, apiBaseUrl: 'http://api.test', headers: () => ({}), dictionaries: {}, users: USERS, me: { id: 10, department_id: 1 },
        meta: {}, settings: {}, staffed: {}, onClose: () => {}, onSaved: () => {}, showToast: () => {}, ...props,
    });
}

const editing = (request, observers) => ({ ...answer({ observers, request: { stage: 'initiation', ...request } }) });

test('форма доработки: наблюдатели раскрыты, в кнопке — первое имя и «и ещё N», про закрытый раздел — строка', async () => {
    const html = await form({ data: editing({}, OBSERVERS) });
    const at = html.indexOf('Наблюдатели');
    assert.ok(at > html.indexOf('Дополнительно'), 'поле стоит в «Дополнительно»');
    // Уволенный (Бывшев) в форму не попадает: в списке выбора его нет, снимают его в окне заявки.
    assert.match(text(html), /Продажина Пелагея и ещё 1/);
    assert.doesNotMatch(html, /Бывшев/);
    assert.match(html, /Раздел не открыт: Продажина Пелагея — заявку пока не увидит\./);
    // «Убрать поле» у наблюдателей, которые уже были, нет: крестик снял бы всех разом.
    assert.doesNotMatch(html, /aria-label="Убрать поле Наблюдатели"/);
});

test('форма новой заявки: выбранных наблюдателей можно убрать вместе с полем', () => {
    const source = readFileSync(new URL('../src/components/payments/PaymentRequestForm.jsx', import.meta.url), 'utf8');
    assert.ok(source.includes("{!(key === 'observer_ids' && observersAtOpen.current.length > 0) && ("));
    assert.ok(source.includes(".filter((person) => !person.fired).map((person) => String(person.user_id))"));
});

test('форма доработки без наблюдателей: только чип «+ Наблюдатели», поля нет', async () => {
    const html = await form({ data: editing({}, []) });
    assert.doesNotMatch(html, /Кто будет следить за заявкой/);
    assert.doesNotMatch(html, /aria-label="Убрать поле Наблюдатели"/);
    const chips = [...html.matchAll(/<button[^>]*>(?:<svg[^]*?<\/svg>)?\s*([^<]*)<\/button>/g)].map((m) => m[1].trim());
    assert.ok(chips.includes('Наблюдатели'), `чипы: ${chips.join(' | ')}`);
});

test('форма регулярного платежа: в «Дополнительно» только наблюдатели', async () => {
    const html = await form({ data: editing({ request_kind: 'regular' }, []) });
    const extra = html.slice(html.lastIndexOf('Дополнительно'));
    assert.match(extra, /Наблюдатели/);
    for (const label of ['Проект', 'Филиал / регион', 'Вложения']) assert.doesNotMatch(extra, new RegExp(label));
});

test('форма: разница считается от наблюдателей при открытии — и у закупа, и у регулярного платежа', () => {
    const source = readFileSync(new URL('../src/components/payments/PaymentRequestForm.jsx', import.meta.url), 'utf8');
    assert.ok(source.includes('const observersPayload = () => observersPatch(draft.observer_ids, observersAtOpen.current);'));
    assert.equal((source.match(/\.\.\.observersPayload\(\),/g) || []).length, 2, 'и у закупа, и у регулярного платежа');
    // Точка отсчёта ставится там же, где форма заполняется при открытии, — для новой заявки пустая.
    const fill = source.slice(source.indexOf('if (fillKey !== filledFor) {'), source.indexOf('const set = useCallback'));
    assert.ok(fill.includes('observersAtOpen.current = next.observer_ids;'));
    assert.doesNotMatch(source, /observer_ids:\s*ids/, 'списком целиком форма наблюдателей больше не шлёт');
});
