/*
 * «Оплата счетов»: чего не хватает — красным у самого поля.
 *
 * Просьба владельца 14.10.2026: «при составлении заявки или же при его выполнении выводить
 * красным то, чего не хватает, можно подсвечивать нужные боксы красным и прокручивается вверх
 * автоматически… если несколько, то какие-нибудь стрелки, чтобы можно было просматривать
 * некорректные пункты, если их больше одного».
 *
 * Было: кнопка «Отправить» (и кнопки этапов) серая, пока не заполнено обязательное, внизу серой
 * строкой «Чтобы отправить: …». Стало: кнопка нажимается; не хватает — ничего не уходит, поля,
 * где пусто, обведены красным, под каждым красным написано, чего в нём нет, окно подводит
 * прокрутку к первому, а если таких полей несколько — «Не хватает: N ⌃ ⌄» в нижней панели окна.
 *
 * Здесь: правила с ключом поля (тексты пунктов прежние — их сверяет payments_meta.test.mjs),
 * переход стрелками, отрисовка поля с пунктом и сторожа проводки по исходникам. Прокрутку,
 * вспышку поля и раскладку панели на компьютере и телефоне снимали проходы стенда.
 *
 * Запуск: node --test tests/payments_issues.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
    UNSTAFFED, invoiceIssues, invoiceProblems, issueKeys, issueSentence, issueStep, issuesByKey, legacyIssues, legacyProblems,
    purchaseIssues, purchaseProblems, regularIssues, regularProblems,
} from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешние react и lucide-react не резолвятся.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-issues-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

const bundles = new Map();
async function bundle(entry, name) {
    if (bundles.has(name)) return bundles.get(name);
    const stubPath = join(OUT_DIR, 'axios-stub.mjs');
    writeFileSync(stubPath, 'const never = () => new Promise(() => {});\nexport default { get: never, post: never, patch: never, delete: never };\n');
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
    // Своё имя на процесс: параллельные прогоны (мутации в соседних копиях) не подменяют друг другу сборки.
    const outPath = join(OUT_DIR, `${name}-${process.pid}.mjs`);
    writeFileSync(outPath, result.outputFiles[0].text);
    const loaded = await import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
    bundles.set(name, loaded);
    return loaded;
}

const h = React.createElement;
const keysOf = (issues) => issues.map((item) => item.key);

/* Заполненный новый закуп — как в payments_meta.test.mjs. */
const filledPurchase = (patch = {}) => ({
    request_kind: 'purchase',
    legal_entity_id: 1, department_id: 5, expense_name: 'Бумага А4', category_id: 3,
    justification: 'Закончилась бумага', due_on: '2026-10-20', object_type: 'service', accounting_category: null,
    payment_method: 'invoice',
    items: [{ key: 'i1', name: 'Бумага А4', quantity: '10', unit: 'уп', unit_price: 10600 }],
    offers: [
        { key: 'o1', counterparty_id: 11, supplier_name: '', amount: 106000, is_recommended: true },
        { key: 'o2', counterparty_id: 12, supplier_name: '', amount: 110000, is_recommended: false },
        { key: 'o3', counterparty_id: null, supplier_name: 'ИП Новый', amount: 120000, is_recommended: false },
    ],
    no_alternatives: false, supplier_choice_reason: 'Дешевле остальных',
    ...patch,
});

/* ── правила: у каждого пункта — поле, тексты прежние ─────────────────────── */

test('строки «чего не хватает» — те же пункты одним текстом (сервер и прежние тесты сверяют их)', () => {
    const empty = { request_kind: 'purchase', items: [], offers: [] };
    assert.deepEqual(purchaseProblems(empty), purchaseIssues(empty).map((item) => item.text));
    assert.deepEqual(regularProblems({}), regularIssues({}).map((item) => item.text));
    assert.deepEqual(legacyProblems(empty), legacyIssues(empty).map((item) => item.text));
    assert.deepEqual(invoiceProblems({}, []), invoiceIssues({}, []).map((item) => item.text));
    assert.deepEqual(purchaseIssues(filledPurchase()), []);
});

test('новый закуп: каждый пункт стоит у своего поля формы', () => {
    const cases = [
        [{ legal_entity_id: null }, 'legal_entity_id'], [{ department_id: null }, 'department_id'],
        [{ expense_name: ' ' }, 'expense_name'], [{ category_id: null }, 'category_id'],
        [{ justification: '' }, 'justification'], [{ due_on: '' }, 'due_on'],
        [{ object_type: null }, 'object_type'], [{ object_type: 'goods' }, 'accounting_category'],
        [{ payment_method: null }, 'payment_method'], [{ supplier_choice_reason: '' }, 'supplier_choice_reason'],
        [{ items: [{ key: 'i1', name: '', quantity: '1', unit_price: 0 }] }, 'items'],
    ];
    for (const [patch, key] of cases) assert.deepEqual(keysOf(purchaseIssues(filledPurchase(patch))), [key], key);
    // Некому выполнить — пункт у того выбора, который его вызвал.
    assert.deepEqual(purchaseIssues(filledPurchase({ object_type: 'goods', accounting_category: 'asset' }), { staffed: { asset_keeper: false } }),
        [{ key: 'accounting_category', text: UNSTAFFED.asset_keeper }]);
    const card = filledPurchase({ payment_method: 'card', card_recipient: 'employee', card_holder_user_id: 7, card_number: '4400430212345678', payment_purpose: 'На картриджи' });
    assert.deepEqual(purchaseIssues(card, { staffed: { finance: false } }), [{ key: 'payment_method', text: UNSTAFFED.finance }]);
});

test('позиции: пункт говорит, какую строку и какое её поле обвести', () => {
    assert.deepEqual(purchaseIssues(filledPurchase({ items: [{ key: 'i1', name: '', quantity: '1', unit_price: 0 }] })),
        [{ key: 'items', text: 'добавьте хотя бы одну позицию', part: 'empty' }]);
    const noName = purchaseIssues(filledPurchase({ items: [
        { key: 'a', name: 'Бумага', quantity: '1', unit_price: 100 }, { key: 'b', name: '', quantity: '1', unit_price: 500 },
    ] }));
    assert.deepEqual(noName.map(({ key, part, rows, field }) => ({ key, part, rows, field })), [{ key: 'items', part: 'row', rows: ['b'], field: 'name' }]);
    const zeroQty = purchaseIssues(filledPurchase({ items: [{ key: 'a', name: 'Бумага', quantity: '0', unit_price: 100 }] }));
    assert.equal(zeroQty[0].field, 'quantity');
    assert.equal(purchaseIssues(filledPurchase({ items: [{ key: 'a', name: 'Бумага', quantity: '1', unit_price: -5 }] }))[0].field, 'unit_price');
    // Цены нет ни у одной строки — обводятся цены всех начатых строк.
    const free = purchaseIssues(filledPurchase({ items: [{ key: 'a', name: 'Бумага', quantity: '2', unit_price: 0 }, { key: 'b', name: 'Ручки', quantity: '1', unit_price: 0 }] }));
    assert.deepEqual(free, [{ key: 'items', text: 'укажите цену за единицу', part: 'price', rows: ['a', 'b'] }]);
});

test('поставщики: что именно не так — мало вариантов, пустая стоимость, не отмечен рекомендуемый', () => {
    const two = filledPurchase();
    two.offers = two.offers.slice(0, 2);
    assert.deepEqual(purchaseIssues(two).map(({ key, part }) => [key, part]), [['offers', 'count']]);
    const noPrice = filledPurchase();
    noPrice.offers[1] = { ...noPrice.offers[1], amount: 0 };
    assert.deepEqual(purchaseIssues(noPrice).map(({ key, part, rows }) => [key, part, rows]), [['offers', 'incomplete', ['o2']]]);
    const noStar = filledPurchase();
    noStar.offers = noStar.offers.map((offer) => ({ ...offer, is_recommended: false }));
    assert.deepEqual(purchaseIssues(noStar).map(({ key, part }) => [key, part]), [['offers', 'recommend']]);
    const single = filledPurchase({ no_alternatives: true, no_alternatives_reason: 'other', no_alternatives_comment: '', offers: [] });
    assert.deepEqual(purchaseIssues(single).map(({ key, part }) => [key, part || null]),
        [['no_alternatives_comment', null], ['offers', 'empty']]);
});

test('карта и счёт: номер, получатель, назначение, файл, номер и дата счёта — каждый у своего поля', () => {
    const card = filledPurchase({ payment_method: 'card', card_recipient: null, card_number: '', payment_purpose: '' });
    assert.deepEqual(keysOf(purchaseIssues(card)), ['card_recipient', 'card_number', 'payment_purpose']);
    assert.deepEqual(keysOf(purchaseIssues({ ...card, card_recipient: 'employee', card_holder_user_id: null })),
        ['card_holder_user_id', 'card_number', 'payment_purpose']);
    assert.deepEqual(keysOf(purchaseIssues({ ...card, card_recipient: 'supplier', card_holder_name: '' })),
        ['card_holder_name', 'card_number', 'payment_purpose']);
    assert.deepEqual(keysOf(purchaseIssues(filledPurchase({ invoice_number: '118' }))), ['invoice_file', 'invoice_date']);
    assert.deepEqual(keysOf(invoiceIssues({}, [])), ['invoice_file', 'invoice_number', 'invoice_date']);
});

test('регулярный платёж и заявка первой версии: пункты у полей, которые есть в их форме', () => {
    assert.deepEqual(keysOf(regularIssues({})),
        ['fixed_template_id', 'department_id', 'payment_period', 'amount', 'invoice_file', 'invoice_number', 'invoice_date']);
    // Пробелы в справочнике и «некому поставить на учёт» — у выбора платежа: в заявке этих полей нет.
    const ready = { fixed_template_id: 9, department_id: 5, payment_period: 'октябрь 2026', amount: 480000, invoice_number: 'ОБЛ-1', invoice_date: '2026-10-08' };
    assert.deepEqual(keysOf(regularIssues(ready, { fileKinds: ['invoice'], template: { counterparty_id: null, legal_entity_id: 1 } })), ['fixed_template_id']);
    assert.deepEqual(keysOf(regularIssues(ready, { fileKinds: ['invoice'], template: { counterparty_id: 1, legal_entity_id: 1, accounting_category: 'asset' }, staffed: { asset_keeper: false } })),
        ['fixed_template_id']);
    // Сумма старой заявки: у закупа — позиции, у регулярного платежа — своё поле.
    assert.deepEqual(keysOf(legacyIssues({ request_kind: 'purchase', legal_entity_id: 1, payment_method: 'card', items: [] })), ['items']);
    assert.deepEqual(keysOf(legacyIssues({ request_kind: 'regular', legal_entity_id: 1, payment_method: 'card', amount: 0 })), ['amount']);
});

/* ── под полем и стрелками ─────────────────────────────────────────────────── */

test('под полем — его пункты одной фразой с заглавной буквы', () => {
    assert.equal(issueSentence(['выберите компанию']), 'Выберите компанию');
    assert.equal(issueSentence(['нужно не меньше 3 вариантов поставщиков', 'отметьте рекомендуемого поставщика']),
        'Нужно не меньше 3 вариантов поставщиков; отметьте рекомендуемого поставщика');
    assert.equal(issueSentence('«Бумага»: количество — число больше нуля'), '«Бумага»: количество — число больше нуля');
    assert.equal(issueSentence([]), '');
    const map = issuesByKey([{ key: 'a', text: '1' }, { key: 'b', text: '2' }, { key: 'a', text: '3' }]);
    assert.deepEqual(Object.keys(map), ['a', 'b']);
    assert.deepEqual(map.a.map((item) => item.text), ['1', '3']);
});

test('стрелки идут по полям в порядке экрана, а не в порядке правил', () => {
    const issues = [{ key: 'legal_entity_id' }, { key: 'expense_name' }, { key: 'offers' }, { key: 'expense_name' }, { key: 'payment_method' }];
    const screen = ['expense_name', 'category_id', 'items', 'legal_entity_id', 'department_id', 'offers', 'payment_method'];
    // Одно поле — один шаг, даже если пунктов у него два.
    assert.deepEqual(issueKeys(issues, screen), ['expense_name', 'legal_entity_id', 'offers', 'payment_method']);
    // Порядок экрана неизвестен (первый кадр) — по порядку пунктов; незнакомые полю экрана — в конце.
    assert.deepEqual(issueKeys(issues), ['legal_entity_id', 'expense_name', 'offers', 'payment_method']);
    assert.deepEqual(issueKeys([{ key: 'x' }, { key: 'offers' }], screen), ['offers', 'x']);
});

test('стрелка: по кругу; от исправленного поля — к соседу по месту на экране', () => {
    const screen = ['a', 'b', 'c', 'd', 'e'];
    const keys = ['b', 'd', 'e'];
    assert.equal(issueStep(keys, null, 1, screen), 'b');
    assert.equal(issueStep(keys, null, -1, screen), 'e');
    assert.equal(issueStep(keys, 'b', 1, screen), 'd');
    assert.equal(issueStep(keys, 'e', 1, screen), 'b', 'с последнего — на первое');
    assert.equal(issueStep(keys, 'b', -1, screen), 'e', 'с первого вверх — на последнее');
    // Человек стоял на «c» и заполнил его: «c» из списка ушло, вниз — «d», вверх — «b».
    assert.equal(issueStep(keys, 'c', 1, screen), 'd');
    assert.equal(issueStep(keys, 'c', -1, screen), 'b');
    // Исправленное было последним — вниз по кругу на первое.
    assert.equal(issueStep(['a', 'b'], 'e', 1, screen), 'a');
    assert.equal(issueStep([], 'a', 1, screen), null);
});

/* ── формы этапов и имущество ──────────────────────────────────────────────── */

test('этапы: пункты у полей формы — дата, сумма, файл, количество, имущество', async () => {
    const { stageIssues } = await bundle('src/components/payments/PaymentStageForm.jsx', 'stage');
    const none = new Set();
    assert.deepEqual(keysOf(stageIssues('invoice_payment', { paid: { paid_on: '', paid_amount: 0 }, kinds: none })), ['paid_on', 'paid_amount', 'files']);
    assert.deepEqual(stageIssues('invoice_payment', { paid: { paid_on: '2026-10-14', paid_amount: 100 }, kinds: new Set(['payment_order']) }), []);
    assert.deepEqual(stageIssues('invoice_payment', { paid: { paid_on: '2026-10-14', paid_amount: 100 }, kinds: none }),
        [{ key: 'files', text: 'приложите платёжное поручение' }]);
    assert.deepEqual(keysOf(stageIssues('card_topup', { paid: { paid_on: '', paid_amount: 0 }, kinds: none })), ['paid_on', 'paid_amount', 'files']);
    assert.deepEqual(stageIssues('card_topup', { paid: { paid_on: '2026-10-14', paid_amount: 1 }, kinds: new Set(['transfer_proof']) }), []);
    assert.deepEqual(keysOf(stageIssues('receipt_confirm', { kinds: none })), ['files']);
    assert.deepEqual(stageIssues('receipt_confirm', { kinds: new Set(['receipt']) }), []);
    // Количество — только у товара.
    assert.deepEqual(keysOf(stageIssues('receiving', { request: { object_type: 'goods' }, received: { received_on: '', received_quantity: ' ' } })),
        ['received_on', 'received_quantity']);
    assert.deepEqual(keysOf(stageIssues('receiving', { request: { object_type: 'service' }, received: { received_on: '2026-10-14', received_quantity: '' } })), []);
    assert.deepEqual(keysOf(stageIssues('closing_docs', { request: { closing_docs_status: 'none' } })), ['files']);
    // Скан выбран, но не загружен — пункт про «Загрузить», а не про ожидание.
    assert.match(stageIssues('closing_docs', { request: { closing_docs_status: 'none' }, queued: 1 })[0].text, /Загрузить/);
    assert.match(stageIssues('closing_docs', { request: { closing_docs_status: 'none' }, queued: 0 })[0].text, /дождитесь скана/);
    assert.deepEqual(stageIssues('closing_docs', { request: { closing_docs_status: 'scan' } }), []);
    // У согласования обязательного в форме нет — пунктов нет.
    assert.deepEqual(stageIssues('approval', {}), []);
});

test('имущество: по пункту на пустое поле каждой карточки, повтор инвентарного — у обеих', async () => {
    const { assetIssues, assetIssueKey } = await bundle('src/components/payments/PaymentAssetsEditor.jsx', 'assets');
    const full = {
        key: 'x', name: 'Ноутбук', category_id: 1, serial_number: 'SN1', inventory_number: 'INV-1', received_on: '2026-10-09', cost: 1,
        legal_entity_id: 1, city: 'Алматы', department_id: 2, responsible_user_id: 3, location: 'офис', status: 'in_use',
    };
    assert.deepEqual(assetIssues([full]), []);
    const gaps = assetIssues([{ ...full, key: 'y', serial_number: ' ', city: null }]);
    assert.deepEqual(gaps, [
        { key: 'asset:y:serial_number', text: 'укажите серийный номер' },
        { key: 'asset:y:city', text: 'выберите город' },
    ]);
    assert.equal(assetIssueKey({ key: 'y' }, 'city'), 'asset:y:city');
    const twins = assetIssues([full, { ...full, key: 'z', inventory_number: ' inv-1 ' }]);
    assert.deepEqual(keysOf(twins), ['asset:x:inventory_number', 'asset:z:inventory_number']);
    // Все двенадцать полей п. 10.2, кроме стоимости (ноль — законная цена подарка), обязательны.
    assert.equal(assetIssues([{ key: 'e' }]).length, 11);
});

/* ── поле с пунктом на экране ──────────────────────────────────────────────── */

test('поле с пунктом: строка под ним красная, элементы внутри — в красной рамке; без пункта — как было', async () => {
    const ui = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const { Field, FormInput, FormText, AmountInput, FormSelect, Choice, FilePicker, INVALID_RING } = ui;
    assert.equal(INVALID_RING, 'ring-2 ring-rose-300');
    const options = [{ value: 1, label: 'ТОО' }, { value: 2, label: 'ИП' }];
    const field = (issue) => renderToStaticMarkup(h(Field, { label: 'Компания', anchor: 'legal_entity_id', issue },
        h(FormInput, { value: '', onChange: () => {} }),
        h(FormText, { value: '', onChange: () => {} }),
        h(AmountInput, { value: 0, onChange: () => {} }),
        h(FormSelect, { value: null, onChange: () => {}, options, placeholder: 'Выберите' }),
        h(Choice, { value: null, onChange: () => {}, options }),
        h(FilePicker, { files: [], onChange: () => {}, label: 'Приложить счёт' })));
    const calm = field('');
    assert.match(calm, /data-issue-anchor="legal_entity_id"/);
    assert.doesNotMatch(calm, /data-issue="true"|data-issue-text|aria-invalid|rose/);
    const bad = field('Выберите компанию');
    assert.match(bad, /data-issue="true"/);
    const line = bad.match(/<span id="([^"]+)" class="[^"]*text-rose-600[^"]*" data-issue-text="true">Выберите компанию<\/span><\/label>$/);
    assert.ok(line, bad);
    // Поля ввода ссылаются на строку: читалка произносит причину вместе с полем.
    assert.equal(bad.split(`aria-describedby="${line[1]}"`).length - 1, 4, 'ввод, текст, сумма и файл');
    // Шесть элементов управления — шесть рамок: ввод, текст, сумма, список, сегменты, файл.
    assert.equal(bad.split('ring-2 ring-rose-300').length - 1, 6, bad);
    assert.equal(bad.split('aria-invalid="true"').length - 1, 5, 'у сегментов своей роли поля нет — рамка у обёртки');
    // Явное invalid={false} старше поля: списки позиций и поставщиков красят только нужную строку.
    const explicit = renderToStaticMarkup(h(Field, { label: 'Поставщики', issue: 'Отметьте рекомендуемого' },
        h(AmountInput, { value: 0, onChange: () => {}, invalid: false })));
    assert.doesNotMatch(explicit, /ring-rose-300|aria-invalid/);
    // Без подписи — только место под строку (файл счёта под полями счёта).
    const bare = renderToStaticMarkup(h(Field, { as: 'div', anchor: 'invoice_file', issue: 'Приложите счёт' }, h('i')));
    assert.doesNotMatch(bare, /uppercase/);
    assert.match(bare, /Приложите счёт/);
    // У высокого поля (список поставщиков, позиции) строка — над списком, сразу под подписью.
    const top = renderToStaticMarkup(h(Field, { as: 'div', label: 'Поставщики', issue: 'Нужно не меньше 3', issueFirst: true }, h('i', null, 'список')));
    assert.ok(top.indexOf('Нужно не меньше 3') < top.indexOf('список'), top);
    const bottom = renderToStaticMarkup(h(Field, { as: 'div', label: 'Поставщики', issue: 'Нужно не меньше 3' }, h('i', null, 'список')));
    assert.ok(bottom.indexOf('Нужно не меньше 3') > bottom.indexOf('список'), bottom);
});

test('Field: провайдер стоит всегда — поле ввода не пересоздаётся, когда пункт снимается или появляется', () => {
    /* Обёртка в провайдер только у поля с пунктом меняла тип узла на месте содержимого: React
       пересоздавал поле ввода, и первая буква, снявшая пункт, выбивала из поля («Бумага А4» →
       «Б», разбор 14.10.2026). Отрисовкой на сервере пересоздание не поймать — сторожим исходник;
       поведение снимает проход стенда (печать в красное поле: тот же узел, всё набранное, фокус). */
    const ui = source('src/components/payments/paymentsUi.jsx');
    const field = ui.slice(ui.indexOf('export const Field = ('), ui.indexOf('export const IssueLine = '));
    assert.ok(field.includes('<FieldIssueContext.Provider value={issue ? issueContext : NO_ISSUE}>{children}</FieldIssueContext.Provider>'));
    assert.doesNotMatch(field, /issue \? <FieldIssueContext\.Provider/);
    assert.equal(field.split('{children}').length - 1, 1, 'содержимое рисуется в одном месте дерева');
});

test('подводка к полю: волна, а при «Уменьшить движение» — неподвижная кайма; фокус — только с клавиатуры', async () => {
    const { revealAnchor, fromKeyboard } = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const fake = () => {
        const calls = { scroll: null, animate: null, focus: null, hint: false };
        // «i» подсказки стоит в подписи раньше поля — фокус должен уйти в само поле.
        const hint = { closest: () => ({}), focus: () => { calls.hint = true; } };
        const control = { closest: () => null, focus: (options) => { calls.focus = options; } };
        const el = {
            parentElement: null, offsetHeight: 40,
            scrollIntoView: (options) => { calls.scroll = options; },
            animate: (frames, options) => { calls.animate = { frames, options }; },
            querySelectorAll: () => [hint, control],
        };
        return { el, calls };
    };
    const saved = globalThis.window;
    try {
        globalThis.window = { matchMedia: () => ({ matches: false }) };
        const plain = fake();
        revealAnchor(plain.el);
        assert.deepEqual(plain.calls.scroll, { block: 'center', behavior: 'smooth' });
        assert.match(plain.calls.animate.frames[2].boxShadow, /12px/, 'волна расходится');
        assert.equal(plain.calls.focus, null, 'мышью и пальцем фокус не переносится');
        const keyboard = fake();
        revealAnchor(keyboard.el, { focus: true });
        assert.deepEqual(keyboard.calls.focus, { preventScroll: true });
        assert.equal(keyboard.calls.hint, false, 'фокус — в поле, а не на «i» в подписи');
        globalThis.window = { matchMedia: () => ({ matches: true }) };
        const calm = fake();
        revealAnchor(calm.el);
        assert.equal(calm.calls.scroll.behavior, 'auto');
        const spreads = calm.calls.animate.frames.map((frame) => frame.boxShadow.match(/0 0 0 (\d+)px/)[1]);
        assert.deepEqual([...new Set(spreads)], ['5'], 'кайма не движется — только гаснет');
    } finally {
        globalThis.window = saved;
    }
    assert.equal(fromKeyboard({ detail: 0 }), true);
    assert.equal(fromKeyboard({ detail: 1 }), false);
    assert.equal(fromKeyboard(undefined), false);
});

test('CustomSelect: без invalid кнопка та же, что везде на сайте; с ним — красная рамка, а раскрытый — синяя', async () => {
    const ui = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const select = (props) => renderToStaticMarkup(h(ui.FormSelect, { value: null, onChange: () => {}, options: [{ value: 1, label: 'А' }], ...props }));
    const calm = select({});
    assert.match(calm, /cursor-pointer ring-slate-200\/70 hover:bg-slate-50 active:scale-\[0\.99\]/);
    assert.doesNotMatch(calm, /rose|aria-invalid/);
    const bad = select({ invalid: true });
    assert.match(bad, /cursor-pointer ring-2 ring-rose-300 hover:bg-slate-50/);
    assert.match(bad, /aria-invalid="true"/);
    // Недоступный список рамкой не краснеет: выбрать в нём всё равно нечего.
    assert.doesNotMatch(select({ invalid: true, disabled: true }), /ring-rose-300/);
    const general = source('src/components/ui/CustomSelect.jsx');
    assert.ok(general.includes("`cursor-pointer ${invalid && !open ? 'ring-2 ring-rose-300' : 'ring-slate-200/70'} hover:bg-slate-50 active:scale-[0.99]`"));
    assert.ok(general.includes('aria-invalid={isIos && invalid ? true : undefined}'));
});

test('«Не хватает: N ⌃ ⌄» — только когда полей больше одного', async () => {
    const { IssueNav } = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    assert.equal(renderToStaticMarkup(h(IssueNav, { count: 1, onStep: () => {} })), '');
    assert.equal(renderToStaticMarkup(h(IssueNav, { count: 0, onStep: () => {} })), '');
    const html = renderToStaticMarkup(h(IssueNav, { count: 4, onStep: () => {} }));
    assert.match(html, /data-issue-nav/);
    assert.match(html, /aria-live="polite">Не хватает: 4<\/span>/);
    assert.match(html, /aria-label="К предыдущему полю, где не хватает данных"/);
    assert.match(html, /aria-label="К следующему полю, где не хватает данных"/);
    // На телефоне — своей строкой во всю ширину над кнопками.
    assert.match(html, /max-sm:order-first max-sm:w-full max-sm:justify-between/);
});

/* ── проводка: кнопки больше не серые, стрелки на месте ───────────────────── */

test('форма заявки: «Отправить» нажимается, серой строки «Чтобы отправить» нет, стрелки в подвале', () => {
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    assert.ok(form.includes('<button type="button" className={iosBtnPrimary} onClick={trySubmit} disabled={busy}>'));
    assert.doesNotMatch(form, /problems\.length|Чтобы отправить: \{/);
    // Не хватает — ничего не уходит: красные поля и подводка к первому (с клавиатуры — с фокусом).
    const trySubmit = form.slice(form.indexOf('const trySubmit = (event) => {'), form.indexOf('const save = async (submit) => {'));
    assert.ok(trySubmit.includes('if (issues.length) {') && trySubmit.includes('setReveal(true);')
        && trySubmit.includes('nav.first({ focus: fromKeyboard(event) });'));
    assert.ok(trySubmit.indexOf('return;') < trySubmit.indexOf('save(true);'), 'с пустыми полями заявка не должна уходить');
    assert.ok(form.includes('{reveal && keys.length > 1 && ('));
    assert.ok(form.includes('<IssueNav count={keys.length} onStep={nav.step} />'));
    // Уже 768 px стрелки — своей строкой над кнопками: в ряду с тремя кнопками они вытесняли «Отправить».
    assert.ok(form.includes('<div className="mr-auto max-md:order-first max-md:mr-0 max-md:w-full">'));
    // У списков поставщиков и позиций строка «чего не хватает» — над списком.
    assert.ok(form.includes("anchor=\"items\" issue={issueOf('items')} issueFirst>"));
    assert.ok(form.includes("anchor=\"offers\" issue={issueOf('offers')} issueFirst>"));
    // До первой попытки поля не краснеют.
    assert.ok(form.includes("const issueOf = (key) => (reveal && byKey[key] ? issueSentence(byKey[key].map((item) => item.text)) : '');"));
    // Каждое поле, о котором бывает пункт, стоит на экране своим якорем.
    for (const key of ['legal_entity_id', 'department_id', 'expense_name', 'category_id', 'justification', 'items', 'due_on', 'object_type',
        'accounting_category', 'payment_method', 'no_alternatives_reason', 'no_alternatives_comment', 'offers', 'supplier_choice_reason',
        'card_recipient', 'card_holder_user_id', 'card_holder_name', 'card_number', 'payment_purpose', 'invoice_file', 'invoice_number',
        'invoice_date', 'fixed_template_id', 'payment_period', 'amount']) {
        assert.ok(form.includes(`anchor="${key}" issue={issueOf('${key}')}`), key);
    }
});

test('формы этапов: кнопки этапа нажимаются, пункты у полей, стрелки — в нижней панели окна заявки', () => {
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    assert.doesNotMatch(stage, /missing\.length|disabled=\{busy \|\| !received_\}|disabled=\{busy \|\| !ready\}/);
    for (const action of ["act('pay', paid)", "act('top_up', paid)", "act('provide')", "act('confirm', received)",
        "act('register', { assets: assets.map(assetPayload) })", "act('docs_close', {}, false)"]) {
        assert.ok(stage.includes(`disabled={busy} onClick={attempt(() => ${action})}`), action);
    }
    const attempt = stage.slice(stage.indexOf('const attempt = (run) => (event) => {'), stage.indexOf('const act = '));
    assert.ok(attempt.includes('if (issues.length) { setMode(null); setReveal(true); nav.first({ focus: fromKeyboard(event) }); return; }'));
    // Открыли панель «вернуть / запросить информацию» — красные поля этапа гаснут.
    assert.ok(attempt.includes('const toggleMode = (next) => { setMode((prev) => (prev === next ? null : next)); setReveal(false); };'));
    assert.equal(stage.split("onClick={() => toggleMode('request_info')}").length - 1, 2);
    assert.ok(stage.includes("onClick={() => toggleMode('return')}") && stage.includes("onClick={() => toggleMode('reject')}"));
    assert.doesNotMatch(stage, /onClick=\{\(\) => setMode\(mode/);
    assert.ok(stage.includes('createPortal(<div className="order-first w-full"><IssueNav count={keys.length} onStep={nav.step} /></div>, issueSlot)'));
    // Возврат, отклонение и запрос информации — тоже не серые: пустая причина краснеет.
    assert.ok(stage.includes('disabled={busy} onClick={submit}>'));
    const card = source('src/components/payments/PaymentRequestCard.jsx');
    assert.ok(card.includes('{onTask && <div ref={setIssueSlot} data-issue-slot className="contents" />}'));
    assert.ok(card.includes('issueSlot={issueSlot}'));
    // «Доработать заявку» из задачи инициатора открывает форму сразу на пустых полях.
    assert.ok(stage.includes('onClick={() => onEdit?.({ reveal: problems.length > 0 })}'));
    assert.ok(card.includes('onEdit={(options) => onEdit?.(data, options)}'));
    const view = source('src/components/payments/PaymentsView.jsx');
    assert.ok(view.includes('setRevealIssues(Boolean(options?.reveal));'));
    assert.ok(view.includes('revealIssues={revealIssues}'));
});
