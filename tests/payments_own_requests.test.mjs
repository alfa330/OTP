/*
 * «Оплата счетов»: «Мои заявки» на рабочем столе — просьба владельца 22.10.2026.
 *
 * «В разделе оплата счетов, как человек сразу же сможет [видеть] свою задачу и текущий
 * статус». Рядом с задачами стола стоят отправленные заявки человека, которые идут у других:
 * этап словами и полосой, у кого заявка. Заявка, по которой ждут его самого, стоит в задачах
 * и второй раз не показывается. Здесь — чистые функции (paymentsMeta.js) и первый кадр стола
 * (серверный рендер, как в payments_section_entry): блок стоит сразу — из памяти страницы, а
 * до неё заготовкой точного роста по счётчику `/ping`.
 *
 * Ответ сервера — образец tests/data/payments_desk_mine.json: его же сверяет с настоящей
 * ручкой /desk tests/test_payments_own_requests.py, так что ключи у сервера и здесь одни.
 *
 * Запуск: node --test tests/payments_own_requests.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
    OWN_SKELETON_MAX, deskAnswer, deskLayout, ownRequestStatus, ownSkeletonRows, stageProgress, stageProgressTitle,
} from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const path = (relative) => new URL(relative, ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const source = (relative) => readFileSync(new URL(relative, ROOT), 'utf8');
const SAMPLE = JSON.parse(source('tests/data/payments_desk_mine.json'));

/* ── чистые функции ──────────────────────────────────────────────────────── */

test('ответ /desk → стол: задачи, их число и свои заявки; старый сервер без `mine` — блока нет', () => {
    const answer = deskAnswer(SAMPLE);
    assert.deepEqual(answer.tasks.map((task) => task.id), [55]);
    assert.equal(answer.total, 1);
    assert.deepEqual(answer.mine.items.map((item) => item.id), [40, 31]);
    assert.equal(answer.mine.total, 7);
    // Своя заявка, которая ждёт получения, — в задачах, но не в блоке: каждая заявка один раз.
    assert.ok(!answer.mine.items.some((item) => item.id === 55));
    assert.deepEqual(deskAnswer({ tasks: [{ id: 1 }], total: 1 }), { tasks: [{ id: 1 }], total: 1, mine: { items: [], total: 0 } });
    // Числа — числами, в каком бы виде ни пришли.
    assert.deepEqual(deskAnswer({ total: '2', mine: { items: [], total: '7' } }), { tasks: [], total: 2, mine: { items: [], total: 7 } });
    for (const junk of [null, undefined, {}, { tasks: 'нет', mine: { items: 'нет' } }]) {
        assert.deepEqual(deskAnswer(junk), { tasks: [], total: 0, mine: { items: [], total: 0 } }, JSON.stringify(junk));
    }
});

test('строка своей заявки: этап и у кого — имя согласующего или подразделение, без повторов', () => {
    const [atApprover, atAccounting] = SAMPLE.mine.items;
    assert.deepEqual(ownRequestStatus(atApprover), { label: 'Согласование', who: 'Утвердова Ульяна' });
    assert.deepEqual(ownRequestStatus(atAccounting), { label: 'Оплата', who: 'Бухгалтерия' });
    // Бухгалтерия на «Согласовании» — это уточнение компании, а не согласование.
    const entity = { ...atAccounting, stage: 'approval', stage_label: 'Согласование', current_kind: 'entity_clarify', current_assignee_name: null };
    assert.deepEqual(ownRequestStatus(entity), { label: 'Уточнение компании', who: 'Бухгалтерия' });
    // Исполнителя нет — только этап, без «· —»; роль, названная как этап, дважды не пишется.
    assert.deepEqual(ownRequestStatus({ ...atApprover, current_role: null, current_assignee_name: null }), { label: 'Согласование', who: '' });
    assert.deepEqual(ownRequestStatus({ ...atAccounting, stage_label: 'Бухгалтерия' }), { label: 'Бухгалтерия', who: '' });
    assert.deepEqual(ownRequestStatus(null), { label: '', who: '' });
});

test('полоса: этапы этой заявки без пропущенных, текущий и подсказка словами', () => {
    const progress = stageProgress(SAMPLE.mine.items[1].lifecycle);
    assert.deepEqual(progress.steps.map((stage) => stage.code), ['initiation', 'approval', 'payment', 'receiving', 'closing', 'closed']);
    assert.equal(progress.total, 6);
    assert.equal(progress.current, 2);
    assert.equal(progress.label, 'Оплата');
    assert.equal(stageProgressTitle(progress),
        'Инициация закупа ✓ → Согласование ✓ → Оплата — сейчас → Получение → Закрывающие документы → Закрытие');
    assert.deepEqual(stageProgress(null), { steps: [], current: -1, total: 0, label: '' });
    assert.equal(stageProgress([{ code: 'closed', label: 'Закрытие', state: 'pending' }]).current, -1);
});

test('заготовка блока — по счётчику `/ping`, не больше строк, чем покажет блок; неизвестно или ноль — нет', () => {
    assert.equal(OWN_SKELETON_MAX, 5, 'столько же, сколько OWN_ON_DESK на сервере (payments/routes.py)');
    assert.match(source('payments/routes.py'), /\nOWN_ON_DESK = 5\n/);
    assert.equal(ownSkeletonRows(2), 2);
    assert.equal(ownSkeletonRows('3'), 3);
    assert.equal(ownSkeletonRows(172), OWN_SKELETON_MAX);
    for (const none of [0, null, undefined, '', 'много', NaN, -1]) assert.equal(ownSkeletonRows(none), 0, String(none));
});

test('место блока: широкий стол — колонка справа, узкий — над задачами, не знали при входе — под ними', () => {
    assert.equal(deskLayout({ withOwn: false, ownKnown: true, roomy: true }), 'plain');
    assert.equal(deskLayout({ withOwn: true, ownKnown: true, roomy: true }), 'columns');
    assert.equal(deskLayout({ withOwn: true, ownKnown: true, roomy: false }), 'own-first');
    // Ширина ещё не измерена (первый проход, серверный рендер) — как узкий: над задачами.
    assert.equal(deskLayout({ withOwn: true, ownKnown: true, roomy: null }), 'own-first');
    // При входе о заявках не знали — где бы ни был стол, блок встаёт под задачами и ничего не сдвигает.
    assert.equal(deskLayout({ withOwn: true, ownKnown: false, roomy: true }), 'stacked');
    assert.equal(deskLayout({ withOwn: true, ownKnown: false, roomy: false }), 'stacked');
});

/* ── первый кадр стола ───────────────────────────────────────────────────── */

const PING = {
    capabilities: { boards: [], can_create: true, can_manage_assets: false, can_manage_cards: false, can_view_assets: false, is_admin: false, sees_all_requests: false },
    me: { id: 7, name: 'Инициаторова Ирина', department_id: 3, department_name: 'IT', has_telegram: true },
    meta: { boards: [] },
    roles: {},
    roles_missing: [],
    roles_staffed: { approver: true, accounting: true, finance: true, asset_keeper: true },
    settings: { due_soon_days: 2, min_suppliers: 3 },
    counters: { desk: 1, own: 2 },
    schema_ready: true,
    storage_ready: true,
    legacy_pending: 0,
    card_key_ready: true,
};

const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-own-requests-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

let bundled = null;
/* Раздел и его память — одной сборкой: у них должен быть общий экземпляр paymentsEntry.js. */
async function section() {
    if (bundled) return bundled;
    const stubPath = join(OUT_DIR, 'axios-stub.mjs');
    writeFileSync(stubPath, 'const never = () => new Promise(() => {});\nexport default { get: never, post: never, patch: never, delete: never };\n');
    const entryPath = join(OUT_DIR, 'entry.mjs');
    writeFileSync(entryPath, [
        `export { default as PaymentsView } from ${JSON.stringify(path('src/components/payments/PaymentsView.jsx'))};`,
        `export { default as PaymentsDesk } from ${JSON.stringify(path('src/components/payments/PaymentsDesk.jsx'))};`,
        `export * as memory from ${JSON.stringify(path('src/components/payments/paymentsEntry.js'))};`,
    ].join('\n'));
    const result = await esbuild.build({
        entryPoints: [entryPath],
        bundle: true,
        write: false,
        format: 'esm',
        platform: 'node',
        target: 'node18',
        jsx: 'transform',
        loader: { '.jsx': 'jsx', '.js': 'jsx', '.css': 'empty' },
        external: ['react', 'react-dom', 'lucide-react'],
        alias: { axios: stubPath },
        logLevel: 'silent',
    });
    const outPath = join(OUT_DIR, 'section.mjs');
    writeFileSync(outPath, result.outputFiles[0].text);
    bundled = await import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
    return bundled;
}

const firstFrame = (PaymentsView) => renderToStaticMarkup(React.createElement(PaymentsView, {
    apiBaseUrl: 'http://stand.invalid', withAccessTokenHeader: () => ({}), showToast: () => {}, userId: 7,
}));
const textOf = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
const ownRows = (html) => [...html.matchAll(/<button[^>]*data-own-request="(\d+)"[^>]*>([\s\S]*?)<\/button>/g)]
    .map((match) => ({ id: Number(match[1]), html: match[2], text: textOf(match[2]).trim() }));
const layoutOf = (html) => (html.match(/data-desk="([\w-]+)"/) || [])[1];

test('повторный вход: «Мои заявки» стоят сразу — над задачами, этап и у кого, из памяти страницы', async () => {
    const { PaymentsView, memory } = await section();
    memory.forgetEntryMemory();
    memory.remember(7, 'ping', PING);
    memory.remember(7, 'desk', deskAnswer(SAMPLE));
    const html = firstFrame(PaymentsView);
    assert.ok(!html.includes('data-desk-skeleton') && !html.includes('data-own-skeleton'), 'из памяти — без заготовок');
    // Ширина в серверном рендере не измерена — узкий стол: блок над задачами.
    assert.equal(layoutOf(html), 'own-first');
    const ownAt = html.indexOf('data-own-requests');
    const tasksAt = html.indexOf('data-desk-tasks');
    assert.ok(ownAt > 0 && tasksAt > ownAt, '«Мои заявки» — над задачами');
    assert.match(textOf(html), / Мои заявки Все Бумага А4 /, 'в заголовке — без числа, которого нет в «Заявках»');
    assert.match(textOf(html.slice(tasksAt)), /Ждут вас .*Подтвердите получение/);
    assert.match(html, /aria-label="Все мои заявки"/);

    const rows = ownRows(html);
    assert.deepEqual(rows.map((row) => row.id), [40, 31], 'порядок — как прислал сервер; №55 — только в задачах');
    assert.match(rows[0].text, /^Бумага А4 №40 · ТОО «Бумага» Согласование · Утвердова Ульяна 25 000/);
    assert.match(rows[1].text, /^Картриджи №31 · ИП Пример Оплата · Бухгалтерия 40 000/, 'сумма — за вычетом возврата');
    // Название — в одну строку: строка всегда одного роста, и заготовка ей равна.
    assert.match(rows[0].html, /class="block truncate text-\[14px\] font-semibold leading-snug text-slate-900" title="Бумага А4"/);
    // Полоса — по отрезку на этап заявки; учёта имущества у услуги нет. Читалке она не нужна.
    const bars = rows.map((row) => [...row.html.matchAll(/data-stage="(\w+)"/g)].map((match) => match[1][0]).join(''));
    assert.deepEqual(bars, ['dcpppp', 'ddcppp']);
    assert.match(rows[1].html, /aria-hidden="true" title="Инициация закупа ✓ → Согласование ✓ → Оплата — сейчас → Получение → Закрывающие документы → Закрытие"/);
    // Цвет у строки один: ни жёлтого, ни синего — по своей заявке здесь ничего не ждут от человека.
    assert.ok(!/amber|text-blue-7/.test(rows.map((row) => row.html).join('')));
    // Счётчика в оболочке нет (её записал раздел до правки), а заявки в памяти страницы есть —
    // о них известно, блок сразу на своём месте.
    memory.remember(7, 'ping', { ...PING, counters: { desk: 1 } });
    assert.equal(layoutOf(firstFrame(PaymentsView)), 'own-first');
    memory.forgetEntryMemory();
});

test('стол сам знает о своих заявках из памяти страницы — без подсказки счётчика', async () => {
    const { PaymentsDesk, memory } = await section();
    memory.forgetEntryMemory();
    memory.remember(7, 'desk', deskAnswer(SAMPLE));
    const desk = (expectedOwn) => renderToStaticMarkup(React.createElement(PaymentsDesk, {
        apiBaseUrl: 'http://stand.invalid', headers: () => ({}), refreshKey: 0, onOpen: () => {}, onCount: () => {},
        memoryKey: 7, expected: null, expectedOwn,
    }));
    assert.equal(layoutOf(desk(null)), 'own-first');
    assert.equal(ownRows(desk(null)).length, 2);
    // Без «Все» (раздел не знает, кто смотрит) блок тот же, только без кнопки.
    assert.ok(!desk(null).includes('aria-label="Все мои заявки"'));
    // Памяти нет, счётчика нет — о заявках не знают: заготовки блока нет.
    memory.forgetEntryMemory();
    assert.equal(layoutOf(desk(null)), 'plain');
    assert.equal(layoutOf(desk(2)), 'own-first');
    memory.forgetEntryMemory();
});

test('своих заявок у других нет — стол как раньше: без подписей и без блока', async () => {
    const { PaymentsView, memory } = await section();
    memory.forgetEntryMemory();
    memory.remember(7, 'ping', { ...PING, counters: { desk: 1, own: 0 } });
    memory.remember(7, 'desk', deskAnswer({ ...SAMPLE, mine: { items: [], total: 0 } }));
    const html = firstFrame(PaymentsView);
    assert.equal(layoutOf(html), 'plain');
    assert.ok(!html.includes('data-own-requests') && !html.includes('data-desk-tasks'));
    assert.ok(!textOf(html).includes('Ждут вас') && !textOf(html).includes('Мои заявки'));
    assert.match(html, /data-task="receiving"/);
    // Память, записанная до правки (без `mine`), — то же самое.
    memory.remember(7, 'desk', { tasks: SAMPLE.tasks, total: 1 });
    assert.equal(layoutOf(firstFrame(PaymentsView)), 'plain');
    memory.forgetEntryMemory();
});

test('первый вход после перезагрузки: заготовка блока точного роста над заготовкой задач', async () => {
    const { PaymentsView, memory } = await section();
    memory.forgetEntryMemory();
    memory.remember(7, 'ping', { ...PING, counters: { desk: 2, own: 3 } });
    const html = firstFrame(PaymentsView);
    assert.equal(layoutOf(html), 'own-first');
    assert.ok(html.indexOf('data-own-skeleton') < html.indexOf('data-desk-skeleton'), 'заготовка блока — над заготовкой задач');
    assert.equal((html.match(/data-own-bone/g) || []).length, 3);
    // Строка заготовки — те же кегли и межстрочия, что у настоящей строки: заявки встают без сдвига.
    const bone = html.slice(html.indexOf('data-own-bone'));
    for (const line of ['block text-[14px] leading-snug', 'mt-0.5 block text-[13px]', 'mt-2 h-1 w-full max-w-[300px]', 'mt-1.5 block text-[12.5px] leading-[18px]', 'mt-1.5 block text-[14px] sm:hidden']) {
        assert.ok(bone.includes(line), `в заготовке строки нет «${line}»`);
    }
    // На телефоне у задачи сумма — своей строкой; у заготовки задач она тоже есть.
    assert.match(html.slice(html.indexOf('data-desk-skeleton')), /mt-1\.5 flex h-\[21px\] items-center sm:hidden/);
    // Заявок больше, чем покажет блок, — заготовка ростом в блок, а не в счётчик.
    memory.forgetEntryMemory();
    memory.remember(7, 'ping', { ...PING, counters: { desk: 2, own: 9 } });
    assert.equal((firstFrame(PaymentsView).match(/data-own-bone/g) || []).length, OWN_SKELETON_MAX);
    // Своих заявок счётчик не обещает или его нет (оболочка до правки) — заготовки блока нет.
    for (const counters of [{ desk: 2, own: 0 }, { desk: 2 }]) {
        memory.forgetEntryMemory();
        memory.remember(7, 'ping', { ...PING, counters });
        const plain = firstFrame(PaymentsView);
        assert.ok(!plain.includes('data-own-requests'), JSON.stringify(counters));
        assert.equal(layoutOf(plain), 'plain');
    }
    memory.forgetEntryMemory();
});

test('задач нет, а свои заявки есть — пустота одной строкой, не крупной картинкой', async () => {
    const { PaymentsView, memory } = await section();
    memory.forgetEntryMemory();
    memory.remember(7, 'ping', { ...PING, counters: { desk: 0, own: 2 } });
    memory.remember(7, 'desk', deskAnswer({ ...SAMPLE, tasks: [], total: 0 }));
    const html = firstFrame(PaymentsView);
    const empty = html.match(/<div[^>]*data-desk-empty[^>]*>([\s\S]*?)<\/div>/);
    assert.ok(empty, 'пустого стола нет');
    assert.equal(textOf(empty[1]).trim(), 'Задач для вас сейчас нет');
    assert.ok(!empty[0].includes('py-14'), 'крупная пустота занимала бы пол-экрана');
    assert.equal(ownRows(html).length, 2);
    // Без своих заявок пустой стол — прежний, крупный.
    memory.remember(7, 'desk', deskAnswer({ ...SAMPLE, tasks: [], total: 0, mine: { items: [], total: 0 } }));
    assert.match(firstFrame(PaymentsView), /py-14[^"]*" data-desk-empty|data-desk-empty[^>]*py-14/);
    memory.forgetEntryMemory();
});

test('стол берёт ответ через deskAnswer и помнит его целиком; «Все» — «Заявки» с отбором «Инициатор — я»', () => {
    const desk = source('src/components/payments/PaymentsDesk.jsx');
    assert.match(desk, /const answer = deskAnswer\(response\.data\);[\s\S]*?setOwn\(answer\.mine\);[\s\S]*?if \(pageNumber === 1\) remember\(memoryKey, 'desk', answer\);/);
    assert.match(desk, /const \[own, setOwn\] = useState\(\(\) => \(remembered\?\.mine \? deskAnswer\(remembered\)\.mine : null\)\);/);
    // Колонки на широком столе — стилем: классы сеток телефонная оболочка перестраивает по-своему.
    assert.match(desk, /const DESK_COLUMNS = \{ display: 'grid', gridTemplateColumns: 'minmax\(0, 7fr\) minmax\(0, 5fr\)'/);
    assert.match(desk, /style=\{columns \? DESK_COLUMNS : undefined\}/);
    assert.ok(!/grid-cols-\[/.test(desk), 'сетка классом схлопнулась бы оболочкой телефона');
    const view = source('src/components/payments/PaymentsView.jsx');
    assert.match(view, /setSearch\(''\);\s*setQuery\(''\);\s*setState\('all'\);\s*setFilters\(\{ \.\.\.EMPTY_FILTERS, initiator_id: meId \}\);\s*changeTab\('requests'\);/);
    assert.match(view, /onShowOwn=\{meId \? showOwnRequests : null\}/);
    assert.match(view, /expectedOwn=\{ownCount\}/);
    assert.match(view, /setOwnCount\(Number\(response\.data\?\.counters\?\.own\) \|\| 0\);/);
});
