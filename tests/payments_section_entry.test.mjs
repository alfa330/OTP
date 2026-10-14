/*
 * «Оплата счетов»: раздел открывается сразу в своём виде.
 *
 * Жалоба владельца 13.10.2026: «при входе в раздел имеется микро пролаг, то есть сначала
 * выходит, что типа сверху два раздела — мои задачи и заявки». Набор вкладок, кнопка
 * «Создать заявку» и предупреждения приходят с сервера (`/ping`); до ответа раздел рисовал
 * две вкладки «по умолчанию» и надпись «Загружаем задачи…», а через треть секунды
 * перестраивался: вкладок становилось восемь, появлялась кнопка, надпись сменялась списком.
 *
 * Теперь раздел помнит прошлый ответ (src/components/payments/paymentsEntry.js) и первый
 * кадр рисует им; когда помнить нечего — стоит заготовка, а не неполный раздел.
 *
 * Серверный рендер (react-dom/server) эффектов не запускает: компонент остаётся ровно в том
 * состоянии, в каком человек видит его в первый кадр после щелчка в меню, — запросы ещё не
 * ушли. Это состояние здесь и проверяется; кадры настоящего входа снимает проход стенда.
 *
 * Запуск: node --test tests/payments_section_entry.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
    ENTRY_STORAGE_KEY, entryShell, forgetEntryMemory, recall, recallShell, remember, rememberPing,
} from '../src/components/payments/paymentsEntry.js';
import { DESK_SKELETON_MAX, deskSkeletonRows } from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const path = (relative) => new URL(relative, ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const source = (relative) => readFileSync(new URL(relative, ROOT), 'utf8');

/* Ответ `/ping` администратора — как на стенде, с выдуманными людьми. */
const PING = {
    capabilities: {
        boards: ['approval', 'accounting', 'finance'], can_create: true, can_manage_assets: true,
        can_manage_cards: true, can_view_assets: true, is_admin: true, sees_all_requests: true,
    },
    me: { id: 7, name: 'Карабек Ержан', department_id: 3, department_name: 'IT', has_telegram: true },
    meta: {
        boards: [
            { code: 'approval', short: 'Согласование', title: 'Согласование закупа' },
            { code: 'accounting', short: 'Бухгалтерия', title: 'Бухгалтерия / Оплата счетов' },
            { code: 'finance', short: 'Финансовый отдел', title: 'Финансовый отдел / Пополнение карт' },
        ],
    },
    roles: {
        approver: [{ user_id: 9, name: 'Иванова Айгерим' }],
        accounting: [{ user_id: 11, name: 'Петров Данияр' }],
    },
    roles_missing: [],
    roles_staffed: { approver: true, accounting: true, finance: false, asset_keeper: false },
    settings: { due_soon_days: 2, min_suppliers: 3 },
    counters: { desk: 2 },
    schema_ready: true,
    storage_ready: true,
    legacy_pending: 0,
    card_key_ready: true,
};
const TABS = ['Мои задачи', 'Согласование', 'Бухгалтерия', 'Финансовый отдел', 'Заявки', 'Имущество', 'Справочники', 'Участники'];

const DESK = {
    total: 2,
    tasks: [
        { id: 31, task: 'approval', title: 'Согласуйте закуп', expense_name: 'Бумага А4', counterparty_name: 'ТОО «Образец»', amount: 10000 },
        { id: 32, task: 'invoice_payment', title: 'Оплатите счёт', expense_name: 'Картриджи', counterparty_name: 'ИП Пример', amount: 45000 },
    ],
};

const fakeStorage = (initial = {}) => {
    const data = { ...initial };
    return {
        data,
        getItem: (key) => (key in data ? data[key] : null),
        setItem: (key, value) => { data[key] = String(value); },
        removeItem: (key) => { delete data[key]; },
    };
};

/* ── что и куда запоминается ─────────────────────────────────────────────── */

test('оболочка ответа: права, названия и счётчики — без имён', () => {
    const shell = entryShell(PING);
    assert.deepEqual(Object.keys(shell).sort(), [
        'capabilities', 'card_key_ready', 'counters', 'legacy_pending', 'meta', 'roles_missing', 'roles_staffed',
        'schema_ready', 'settings', 'storage_ready',
    ]);
    // Ни себя, ни участников процесса: запись переживает перезагрузку и лежит в браузере.
    assert.equal(shell.me, undefined);
    assert.equal(shell.roles, undefined);
    const text = JSON.stringify(shell);
    for (const name of ['Карабек Ержан', 'Иванова Айгерим', 'Петров Данияр']) assert.ok(!text.includes(name), `в оболочке осталось имя «${name}»`);
    assert.deepEqual(shell.capabilities, PING.capabilities);
    assert.equal(shell.counters.desk, 2);
    // Не ответ сервера — не оболочка.
    for (const junk of [null, undefined, '', 'ok', 5, [], {}, { capabilities: null }, { capabilities: 'all' }]) assert.equal(entryShell(junk), null);
});

test('память страницы: своя у каждого человека, чужая не читается', () => {
    forgetEntryMemory();
    assert.equal(recall(7, 'ping'), null);
    remember(7, 'ping', PING);
    remember(7, 'desk', DESK);
    assert.equal(recall(7, 'ping'), PING);
    assert.equal(recall(7, 'desk'), DESK);
    assert.equal(recall('7', 'ping'), PING);            // номер приходит и числом, и строкой
    assert.equal(recall(7, 'users'), null);
    // Другой человек на той же странице не видит ничего…
    assert.equal(recall(8, 'ping'), null);
    assert.equal(recall(8, 'desk'), null);
    // …а его первая же запись стирает прежнюю: память держит одного.
    remember(8, 'desk', { tasks: [], total: 0 });
    assert.equal(recall(7, 'ping'), null);
    assert.equal(recall(7, 'desk'), null);
    assert.deepEqual(recall(8, 'desk'), { tasks: [], total: 0 });
    // И чужое ему по наследству не достаётся: права и справочники прежнего человека стёрты.
    assert.equal(recall(8, 'ping'), null);
    // Кто смотрит — неизвестно: ничего не запоминается и не вспоминается.
    for (const nobody of [null, undefined, '']) {
        remember(nobody, 'ping', PING);
        assert.equal(recall(nobody, 'ping'), null);
    }
    assert.deepEqual(recall(8, 'desk'), { tasks: [], total: 0 });
    forgetEntryMemory();
});

test('свежий ответ: целиком в память страницы, оболочкой — в хранилище браузера', () => {
    forgetEntryMemory();
    const storage = fakeStorage();
    rememberPing(7, PING, storage);
    assert.equal(recall(7, 'ping'), PING);
    const saved = JSON.parse(storage.data[ENTRY_STORAGE_KEY]);
    assert.equal(saved.user, '7');
    assert.deepEqual(saved.shell, entryShell(PING));
    for (const name of ['Карабек Ержан', 'Иванова Айгерим', 'Петров Данияр']) assert.ok(!storage.data[ENTRY_STORAGE_KEY].includes(name));
    // После перезагрузки страницы память пуста, оболочка — на месте.
    forgetEntryMemory();
    assert.equal(recall(7, 'ping'), null);
    assert.deepEqual(recallShell(7, storage), entryShell(PING));
    assert.deepEqual(recallShell('7', storage), entryShell(PING));
    // Другому человеку на этом же компьютере чужая оболочка не достаётся.
    assert.equal(recallShell(8, storage), null);
    assert.equal(recallShell(null, storage), null);
    // Его вход запись заменяет — она одна.
    rememberPing(8, { ...PING, capabilities: { boards: [], can_create: true } }, storage);
    assert.equal(recallShell(7, storage), null);
    assert.deepEqual(recallShell(8, storage).capabilities, { boards: [], can_create: true });
    forgetEntryMemory();
});

test('хранилище закрыто, переполнено или испорчено — раздел работает по памяти страницы', () => {
    forgetEntryMemory();
    // Нет хранилища вовсе (в Node нет window — то же, что закрытое хранилище в браузере).
    rememberPing(7, PING);
    assert.equal(recall(7, 'ping'), PING);
    assert.equal(recallShell(7), null);
    assert.equal(recallShell(7, null), null);
    // Запись не проходит.
    const full = { getItem: () => null, setItem: () => { throw new Error('QuotaExceededError'); } };
    assert.doesNotThrow(() => rememberPing(7, PING, full));
    assert.equal(recall(7, 'ping'), PING);
    // Чтение бросает или отдаёт мусор.
    assert.equal(recallShell(7, { getItem: () => { throw new Error('SecurityError'); } }), null);
    for (const junk of ['', 'null', '{', '[]', '"7"', '{"user":"7"}', '{"user":"7","shell":null}', '{"user":"7","shell":{"meta":{}}}', '{"user":7,"shell":{"capabilities":{}}}']) {
        assert.equal(recallShell(7, fakeStorage({ [ENTRY_STORAGE_KEY]: junk })), null, junk);
    }
    // Не ответ сервера не запоминается: память остаётся прежней.
    rememberPing(7, { error: 'forbidden' }, fakeStorage());
    assert.equal(recall(7, 'ping'), PING);
    forgetEntryMemory();
});

test('заготовка «Моих задач»: столько строк, сколько задач на счётчике вкладки', () => {
    assert.equal(deskSkeletonRows(2), 2);
    assert.equal(deskSkeletonRows(1), 1);
    assert.equal(deskSkeletonRows('4'), 4);
    // Длинный стол — не длиннее экрана.
    assert.equal(deskSkeletonRows(20), DESK_SKELETON_MAX);
    assert.equal(deskSkeletonRows(137), DESK_SKELETON_MAX);
    // Счётчик говорит «ноль» — заготовки нет: сразу пустой стол.
    assert.equal(deskSkeletonRows(0), 0);
    // Счётчик неизвестен (первый вход на устройстве) — три строки.
    for (const unknown of [null, undefined, '', 'много', NaN, -1]) assert.equal(deskSkeletonRows(unknown), 3);
});

/* ── первый кадр раздела ─────────────────────────────────────────────────── */

// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешние react и lucide-react не
// резолвятся. Каталог .cache не отслеживается git.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-entry-render-test/', import.meta.url)
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

const firstFrame = (PaymentsView, userId) => renderToStaticMarkup(React.createElement(PaymentsView, {
    apiBaseUrl: 'http://stand.invalid', withAccessTokenHeader: () => ({}), showToast: () => {}, userId,
}));
const tabsOf = (html) => [...html.matchAll(/<button[^>]*role="tab"[^>]*>(.*?)<\/button>/g)].map((m) => m[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim());
const hasCreateButton = (html) => /<button[^>]*>(?:<svg[\s\S]*?<\/svg>)?\s*Создать заявку<\/button>/.test(html);

test('первый вход на устройстве: заготовка раздела, а не две вкладки «по умолчанию»', async () => {
    const { PaymentsView, memory } = await section();
    memory.forgetEntryMemory();
    const html = firstFrame(PaymentsView, 7);
    assert.match(html, /Оплата счетов/);
    // Ни одной вкладки: какие положены человеку, ещё не известно.
    assert.deepEqual(tabsOf(html), []);
    assert.match(html, /data-tabs-skeleton/);
    // На месте кнопки — заготовка её же размера: те же поля и тот же (невидимый) текст.
    assert.ok(!hasCreateButton(html));
    const bone = html.match(/<span[^>]*data-create-skeleton[^>]*>[\s\S]*?Создать заявку<\/span><\/span>/);
    assert.ok(bone, 'заготовки кнопки нет');
    for (const size of ['px-4', 'py-2.5', 'text-[13.5px]', 'rounded-xl', 'w-full', 'sm:w-auto']) assert.ok(bone[0].includes(size), `у заготовки кнопки нет ${size}`);
    assert.match(bone[0], /class="invisible/);
    // Список — заготовкой в три строки, без надписи «Загружаем задачи…».
    const desk = html.match(/<div[^>]*data-desk-skeleton[^>]*>([\s\S]*?)<\/div><\/div>(?!<div class="flex items-stretch)/);
    assert.ok(desk, 'заготовки списка нет');
    assert.equal((html.match(/class="flex items-stretch gap-3 pl-4"/g) || []).length, 3);
    assert.ok(!html.includes('Загружаем задачи…'));
});

test('повторный вход: раздел рисуется сразу готовым — вкладки, кнопка и задачи из памяти страницы', async () => {
    const { PaymentsView, memory } = await section();
    memory.forgetEntryMemory();
    memory.remember(7, 'ping', PING);
    memory.remember(7, 'desk', DESK);
    const html = firstFrame(PaymentsView, 7);
    assert.deepEqual(tabsOf(html), ['Мои задачи 2', ...TABS.slice(1)]);
    assert.ok(hasCreateButton(html));
    assert.ok(!html.includes('data-tabs-skeleton') && !html.includes('data-create-skeleton') && !html.includes('data-desk-skeleton'));
    assert.match(html, /Согласуйте закуп/);
    assert.match(html, /Оплатите счёт/);
    assert.equal((html.match(/data-task="/g) || []).length, 2);
    // Другому человеку на той же странице чужое не показывается: у него заготовка.
    const other = firstFrame(PaymentsView, 8);
    assert.deepEqual(tabsOf(other), []);
    assert.match(other, /data-tabs-skeleton/);
    assert.ok(!other.includes('Согласуйте закуп'));
    memory.forgetEntryMemory();
});

test('первый вход после перезагрузки: вкладки и кнопка — по оболочке из хранилища, список — заготовкой по счётчику', async () => {
    const { PaymentsView, memory } = await section();
    memory.forgetEntryMemory();
    const storage = fakeStorage();
    memory.rememberPing(7, { ...PING, storage_ready: false }, storage);
    memory.forgetEntryMemory();                      // страницу перезагрузили: память пуста
    const hadWindow = 'window' in globalThis;
    const before = globalThis.window;
    globalThis.window = { localStorage: storage };
    let html;
    let other;
    try {
        html = firstFrame(PaymentsView, 7);
        other = firstFrame(PaymentsView, 8);
    } finally {
        if (hadWindow) globalThis.window = before; else delete globalThis.window;
    }
    assert.deepEqual(tabsOf(html), ['Мои задачи 2', ...TABS.slice(1)]);
    assert.ok(hasCreateButton(html));
    assert.ok(!html.includes('data-tabs-skeleton') && !html.includes('data-create-skeleton'));
    // Предупреждение раздела тоже стоит сразу — оно в оболочке, а не только в свежем ответе.
    assert.match(html, /Хранилище файлов не настроено/);
    // Задач на счётчике две — в заготовке две строки.
    assert.match(html, /data-desk-skeleton/);
    assert.equal((html.match(/class="flex items-stretch gap-3 pl-4"/g) || []).length, 2);
    // Чужая запись в хранилище не читается.
    assert.deepEqual(tabsOf(other), []);
    assert.match(other, /data-tabs-skeleton/);
});

test('счётчик говорит «задач нет» — сразу пустой стол, без заготовки', async () => {
    const { PaymentsView, memory } = await section();
    memory.forgetEntryMemory();
    memory.remember(7, 'ping', { ...PING, counters: { desk: 0 } });
    const html = firstFrame(PaymentsView, 7);
    assert.deepEqual(tabsOf(html)[0], 'Мои задачи');
    assert.ok(!html.includes('data-desk-skeleton'));
    assert.match(html, /Задач для вас сейчас нет/);
    memory.forgetEntryMemory();
});

test('предупреждения раздела стоят с первого кадра, а не появляются после ответа', async () => {
    const { PaymentsView, memory } = await section();
    memory.forgetEntryMemory();
    memory.remember(7, 'ping', { ...PING, roles_missing: ['Бухгалтерия'], storage_ready: false });
    const html = firstFrame(PaymentsView, 7);
    assert.match(html, /Заявки пока не завести: не назначены участники — Бухгалтерия\./);
    assert.match(html, /Хранилище файлов не настроено/);
    memory.forgetEntryMemory();
});

/* ── вход по ссылке (адрес раздела — paymentsLink.js) ───────────────────── */

const selectedTabOf = (html) => {
    const match = html.match(/<button[^>]*role="tab"[^>]*aria-selected="true"[^>]*>(.*?)<\/button>/);
    return match ? match[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim() : null;
};

/* Первый кадр при заданном адресе: окно с адресной строкой и (не)пустым хранилищем.
   Доска и справочники зовут useLayoutEffect, и серверный рендер React предупреждает, что он
   ничего не делает, — для первого кадра это и нужно; остальные ошибки проходят как есть. */
const frameAt = (PaymentsView, search, storage = fakeStorage()) => {
    const hadWindow = 'window' in globalThis;
    const before = globalThis.window;
    const consoleError = console.error;
    globalThis.window = { localStorage: storage, location: { search } };
    console.error = (...args) => {
        if (String(args[0]).startsWith('Warning: useLayoutEffect does nothing on the server')) return;
        consoleError(...args);
    };
    try {
        return firstFrame(PaymentsView, 7);
    } finally {
        console.error = consoleError;
        if (hadWindow) globalThis.window = before; else delete globalThis.window;
    }
};

test('ссылка на вкладку: первый кадр — сразу она, а не «Мои задачи»', async () => {
    const { PaymentsView, memory } = await section();
    memory.forgetEntryMemory();
    memory.remember(7, 'ping', PING);
    memory.remember(7, 'desk', DESK);
    const requests = frameAt(PaymentsView, '?view=payments&section=requests&request=3');
    assert.equal(selectedTabOf(requests), 'Заявки');
    assert.ok(!requests.includes('Согласуйте закуп'), 'под вкладкой «Заявки» нарисован стол');
    assert.ok(!requests.includes('data-link-pending'));
    assert.equal(selectedTabOf(frameAt(PaymentsView, '?view=payments&section=accounting')), 'Бухгалтерия');
    // Справочник из ссылки выбран в левом списке.
    const routes = frameAt(PaymentsView, '?view=payments&section=dictionaries&dict=routes');
    assert.equal(selectedTabOf(routes), 'Справочники');
    const current = routes.match(/<button[^>]*aria-current="page"[^>]*>([\s\S]*?)<\/button>/);
    assert.ok(current && current[1].includes('Маршруты согласования'), 'в левом списке выбран не тот справочник');
    // Карточка имущества из ссылки открыта сразу.
    const asset = frameAt(PaymentsView, '?view=payments&section=assets&asset=2');
    assert.equal(selectedTabOf(asset), 'Имущество');
    assert.match(asset, /aria-label="Загружаем карточку"/);
    assert.doesNotMatch(frameAt(PaymentsView, '?view=payments&section=assets'), /aria-label="Загружаем карточку"/);
    // Ссылка из Telegram — заявка поверх «Моих задач».
    assert.equal(selectedTabOf(frameAt(PaymentsView, '?view=payments&request=3')), 'Мои задачи 2');
    // Метки чужого раздела (вход из меню, в строке ещё прежний view) не читаются.
    assert.equal(selectedTabOf(frameAt(PaymentsView, '?view=tasks&section=requests')), 'Мои задачи 2');
    memory.forgetEntryMemory();
});

test('ссылка на вкладку, права на которую ещё не известны: заготовка, а не «Мои задачи»', async () => {
    const { PaymentsView, memory } = await section();
    memory.forgetEntryMemory();
    // Первый вход на устройстве: помнить нечего.
    const fresh = frameAt(PaymentsView, '?view=payments&section=assets&asset=2');
    assert.match(fresh, /data-link-pending="tab"/);
    assert.ok(!fresh.includes('data-desk-skeleton'), 'на месте вкладки из ссылки — заготовка стола');
    // Запомненный набор вкладку не знает (права могли дать позже) — ждём ответа сервера,
    // ни одна вкладка не выбрана, в том числе «Мои задачи».
    memory.remember(7, 'ping', { ...PING, capabilities: { ...PING.capabilities, is_admin: false, can_view_assets: false, can_manage_assets: false } });
    const unknown = frameAt(PaymentsView, '?view=payments&section=assets');
    assert.equal(selectedTabOf(unknown), null);
    assert.match(unknown, /data-link-pending="tab"/);
    assert.ok(!tabsOf(unknown).includes('Имущество'));
    // Вкладка «Заявки» есть у всех — её ждать нечего.
    const requests = frameAt(PaymentsView, '?view=payments&section=requests');
    assert.equal(selectedTabOf(requests), 'Заявки');
    assert.ok(!requests.includes('data-link-pending'));
    // Справочник, на который в запомненных правах прав нет («Настройки» — администратору): заготовка
    // на его месте, а не «Поставщики», которые через миг сменились бы им.
    memory.remember(7, 'ping', { ...PING, capabilities: { ...PING.capabilities, is_admin: false } });
    const settings = frameAt(PaymentsView, '?view=payments&section=dictionaries&dict=settings');
    assert.equal(selectedTabOf(settings), 'Справочники');
    assert.match(settings, /data-link-pending="dictionary"/);
    assert.ok(!settings.includes('aria-current="page"'), 'под заготовкой нарисован первый справочник');
    memory.forgetEntryMemory();
});

test('раздел узнаёт, кто его смотрит, от портала; надпись о загрузке не мелькает', () => {
    const app = source('src/App.jsx');
    const at = app.indexOf('<PaymentsView');
    assert.ok(at > 0);
    const mount = app.slice(at, app.indexOf('/>', at));
    assert.ok(mount.includes('userId={user?.id}'), 'портал не передаёт разделу номер пользователя — запоминать не для кого');
    // Код раздела обычно в кеше и приходит за кадр: надпись проявляется только через треть секунды.
    const fallback = app.slice(app.lastIndexOf('<Suspense', at), at);
    assert.ok(fallback.includes('motion-safe:animate-fade-in-soft motion-safe:![animation-delay:300ms]'), fallback);
    const view = source('src/components/payments/PaymentsView.jsx');
    // Свежие ответы запоминаются: права и названия, справочники, сотрудники; стол — в PaymentsDesk.
    assert.ok(view.includes('rememberPing(userId, response.data);'));
    assert.ok(view.includes("remember(userId, 'dictionaries', response.data);"));
    assert.ok(view.includes("remember(userId, 'users', list);"));
    assert.ok(source('src/components/payments/PaymentsDesk.jsx').includes("if (pageNumber === 1) remember(memoryKey, 'desk', { tasks: list, total: count });"));
    // Форма «Создать заявку» ждёт полного ответа: из него она берёт самого человека и его подразделение.
    assert.ok(view.includes('if (!ping) { setCreateWanted(true); return; }'));
    // Список участников — только по полному ответу: по одной оболочке роли выглядели бы пустыми
    // («Пока никого. Без участника этой роли заявки не заводятся»). До него — заготовка.
    assert.match(view, /\{ping \? \(\s*<PaymentsRoles\b[\s\S]*?roles=\{ping\.roles \|\| \{\}\}[\s\S]*?\) : \([\s\S]*?aria-label="Загружаем участников"/);
    assert.ok(!view.includes('roles={ping?.roles || {}}'));
});

test('ленты вкладок на телефоне стоят в краях раздела — без отрицательных полей', () => {
    // В телефонной оболочке отрицательные боковые поля гасятся (mobile-shell.css: `[class^="-mx-"]`),
    // а внутренние остаются: лента с `-mx-3 px-3` стояла на 12 px правее кнопки и карточек.
    const shell = source('src/components/common/mobile-shell.css');
    assert.match(shell, /body\.mobile-shell \.main-content \[class\^="-mx-"\],\s*body\.mobile-shell \.main-content \[class\*=" -mx-"\] \{\s*margin-left: 0 !important;/);
    const view = source('src/components/payments/PaymentsView.jsx');
    const board = source('src/components/payments/PaymentsBoard.jsx');
    assert.ok(view.includes('<div ref={tabsRef} className="mt-4 overflow-x-auto [scrollbar-width:none]">'));
    assert.ok(board.includes('<div ref={stripRef} className="overflow-x-auto [scrollbar-width:none]">'));
    for (const [name, text] of [['PaymentsView.jsx', view], ['PaymentsBoard.jsx', board]]) {
        assert.doesNotMatch(text, /className="-mx-3[ "]|className="[^"]* -mx-3[ "]/, `${name}: лента снова с отрицательным полем`);
    }
});
