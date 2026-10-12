/*
 * Первый кадр окон «Оплаты счетов»: окно заявки и карточка имущества.
 *
 * Жалоба владельца 12.10.2026: «при открытии карточки … мини карточка загрузка и
 * резко она увеличивается». Окно открывалось коротким — заголовок «Заявка»,
 * строка «Загружаем заявку…», без подвала, — а на ответе сервера за один кадр
 * вырастало в несколько раз (на стенде 209 → 920 px) и получало номер,
 * подзаголовок и подвал. Теперь первый кадр — уже окно в свой рост: номер в
 * шапке, заготовка на месте содержимого, подвал с «Готово».
 *
 * Серверный рендер (react-dom/server) эффектов не запускает — компонент
 * остаётся ровно в том состоянии, в каком его видит человек в первый кадр после
 * щелчка: запрос ещё не ушёл, данных нет. Это состояние и проверяем.
 *
 * Запуск: node --test tests/payments_window_first_frame_render.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import { ASSET_SKELETON, REQUEST_SKELETON } from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешние react и
// lucide-react не резолвятся. Каталог .cache не отслеживается git.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-window-render-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

const AXIOS_STUB = `
  const never = () => new Promise(() => {});
  export default { get: never, post: never, patch: never, delete: never };
`;

async function bundle(entry, name) {
    const stubPath = join(OUT_DIR, 'axios-stub.mjs');
    writeFileSync(stubPath, AXIOS_STUB);
    const result = await esbuild.build({
        entryPoints: [new URL(entry, ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
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
    const outPath = join(OUT_DIR, `${name}.mjs`);
    writeFileSync(outPath, result.outputFiles[0].text);
    return import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
}

const props = {
    apiBaseUrl: 'http://api.test',
    headers: () => ({}),
    dictionaries: {},
    users: [],
    me: { id: 1 },
    meta: {},
    showToast: () => {},
    onClose: () => {},
};

const count = (html, pattern) => (html.match(pattern) || []).length;
// Карточка раздела (iosCard) и строка заготовки — по ним считаем, что заготовка полная.
const CARD = /rounded-2xl bg-white ring-1 ring-slate-200\/70/g;
const BONE = /animate-pulse rounded-full bg-slate-200\/70/g;
const rowsOf = (sections) => sections.reduce((sum, section) => sum + (typeof section === 'number' ? section : section.rows), 0);

test('окно заявки с первого кадра стоит в свой рост: номер, заготовка и подвал', async () => {
    const { default: PaymentRequestCard } = await bundle('src/components/payments/PaymentRequestCard.jsx', 'request-card');
    const html = renderToStaticMarkup(React.createElement(PaymentRequestCard, { ...props, open: true, requestId: 471 }));

    // Номер известен до ответа сервера — шапка не меняется, когда заявка придёт.
    assert.match(html, /Заявка №471/);
    // Заготовка названа читалке один раз и занимает место содержимого.
    assert.equal(count(html, /aria-busy="true"/g), 1);
    assert.match(html, /role="status"[^>]*aria-label="Загружаем заявку"|aria-label="Загружаем заявку"[^>]*role="status"/);
    // Сводка и по карточке на каждый раздел; в каждой строке — подпись и значение.
    assert.equal(count(html, CARD), 1 + REQUEST_SKELETON.length);
    assert.ok(count(html, BONE) >= rowsOf(REQUEST_SKELETON) * 2);
    // Подвал стоит сразу: раньше он появлялся вместе с заявкой и добавлял окну высоты.
    assert.match(html, /Готово/);
    // Прежней короткой строки с крутилкой больше нет.
    assert.doesNotMatch(html, /Загружаем заявку…/);
    assert.doesNotMatch(html, /animate-spin/);
});

test('закрытое окно заявки ничего не рисует', async () => {
    const { default: PaymentRequestCard } = await bundle('src/components/payments/PaymentRequestCard.jsx', 'request-card');
    const html = renderToStaticMarkup(React.createElement(PaymentRequestCard, { ...props, open: false, requestId: null }));
    assert.equal(html, '');
});

test('карточка имущества: название и номер из строки реестра, заготовка и подвал', async () => {
    const { AssetCard } = await bundle('src/components/payments/PaymentsAssets.jsx', 'assets');
    const html = renderToStaticMarkup(React.createElement(AssetCard, {
        ...props, assetId: 5, preview: { id: 5, name: 'Монитор Dell 27', inventory_number: 'ИНВ-Б-53960' },
    }));
    assert.match(html, /Монитор Dell 27/);
    assert.match(html, /инв\. № ИНВ-Б-53960/);
    assert.equal(count(html, /aria-busy="true"/g), 1);
    assert.match(html, /aria-label="Загружаем карточку"/);
    assert.equal(count(html, CARD), ASSET_SKELETON.length);
    assert.match(html, /Готово/);
    assert.doesNotMatch(html, /Загружаем…/);
    assert.doesNotMatch(html, /animate-spin/);
});

test('карточка имущества без строки реестра: место под номер держит полоса', async () => {
    const { AssetCard } = await bundle('src/components/payments/PaymentsAssets.jsx', 'assets');
    const html = renderToStaticMarkup(React.createElement(AssetCard, { ...props, assetId: 5 }));
    assert.match(html, /Имущество/);
    // Строка под заголовком есть, и в ней полоса, а не пусто: иначе шапка подросла бы после загрузки.
    assert.match(html, /<p class="truncate text-\[12px\] text-slate-500"><span aria-hidden="true" class="inline-block align-middle animate-pulse/);
});
