/*
 * «Без договора» в «Оплате счетов» (решение владельца 10.11.2026).
 *
 * «Без договора» — отдельный пункт поля «Договор», а не пустое поле: с ним счёт любой суммы
 * уходит в оплату, а утвердитель и бухгалтер видят отметку (при счёте больше 300 000 ₸ —
 * с пометкой). Здесь — подписи и проводка интерфейса: окно бухгалтера и карточка
 * рисуются серверным рендером С ДАННЫМИ, форма и справочник проверяются по исходнику.
 *
 * Запуск: node --test tests/payments_no_contract.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import {
    CONTRACT_ISSUE, CONTRACT_THRESHOLD, HINTS, NO_CONTRACT, NO_CONTRACT_LABEL, contractChoice, contractPick, contractSummary,
    describeEvent, fmtMoney, purchaseIssues, recommendedOffer, regularIssues, supplierKeyOf,
} from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const source = (path) => readFileSync(join(ROOT, path), 'utf8');
// Сборка — внутрь node_modules: из системного temp внешние react и lucide-react не резолвятся.
const OUT_DIR = join(ROOT, 'node_modules/.cache/otp-payments-no-contract-test');
mkdirSync(OUT_DIR, { recursive: true });
const OVER = fmtMoney(CONTRACT_THRESHOLD);

/* ── Подписи ───────────────────────────────────────────────────────────────── */

test('договор словами: номер, «Без договора» с пометкой свыше порога или ничего', () => {
    assert.equal(NO_CONTRACT_LABEL, 'Без договора');
    assert.notEqual(NO_CONTRACT, null, '«Без договора» — не пустое значение списка');
    assert.deepEqual(contractSummary({ contract_number: '12', amount: 900000, no_contract: true }),
        { text: '№12', chosen: true, over: true, marked: false });
    assert.deepEqual(contractSummary({ no_contract: true, amount: 900000 }),
        { text: 'Без договора', chosen: true, over: true, marked: true });
    assert.deepEqual(contractSummary({ no_contract: true, amount: 300000 }),
        { text: 'Без договора', chosen: true, over: false, marked: false }, 'порог — строго больше');
    assert.deepEqual(contractSummary({ amount: 900000 }), { text: '', chosen: false, over: true, marked: false });
    assert.equal(contractSummary({ no_contract: true, amount: 400000 }, 500000).marked, false, 'порог — с сервера');
});

/* ── Выбор в поле «Договор» — правило формы и окна Helpdesk IT (без исходника) ─── */

const offer = (patch) => ({ key: 'k1', counterparty_id: null, supplier_name: '', custom: false, amount: 900000,
    is_recommended: true, ...patch });

test('«Без договора» действует при том поставщике, при котором её выбрали', () => {
    const our = offer({ counterparty_id: 11 });
    const other = offer({ key: 'k2', counterparty_id: 12 });
    const typed = offer({ key: 'k3', custom: true, supplier_name: 'ИП Новый' });
    assert.equal(supplierKeyOf(our), 'id:11');
    assert.equal(supplierKeyOf(typed), 'row:k3', 'вписанный названием — по своей строке, правка названия не сбрасывает');
    assert.equal(supplierKeyOf(null), '');
    const chosen = { ...contractPick(NO_CONTRACT, supplierKeyOf(our)) };
    assert.deepEqual(chosen, { contract_id: null, no_contract: true, no_contract_for: 'id:11' });
    assert.deepEqual(contractChoice(chosen, [], 'id:11'), { contractId: null, noContract: true });
    // Сменили рекомендуемого поставщика — отметка не действует и на сервер уйдёт false.
    assert.deepEqual(contractChoice(chosen, [], supplierKeyOf(other)), { contractId: null, noContract: false });
    // Договор: только из списка нынешнего поставщика; выбор договора снимает отметку.
    const options = [{ value: 3, label: '№12' }];
    const contract = { ...chosen, ...contractPick(3, 'id:11') };
    assert.deepEqual(contract, { contract_id: 3, no_contract: false, no_contract_for: null });
    assert.deepEqual(contractChoice(contract, options, 'id:11'), { contractId: 3, noContract: false });
    assert.deepEqual(contractChoice(contract, [], 'id:12'), { contractId: null, noContract: false });
    // Сохранённая заявка: отметка поставлена при её поставщике — тем же правилом, что recommendedOffer.
    const draft = { no_alternatives: false, offers: [other, { ...our, is_recommended: false }] };
    assert.equal(supplierKeyOf(recommendedOffer(draft)), 'id:12');
});

test('заявку вернули за договором — поле «Договор» в списке недочётов', () => {
    const base = {
        department_id: 1, category_id: 3, expense_name: 'Столы', justification: 'Офис', due_on: '2026-11-30',
        items: [{ key: 'i', name: 'Стол', quantity: '1', unit: 'шт', unit_price: 900000 }], object_type: 'service',
        payment_method: 'invoice', no_alternatives: true, no_alternatives_reason: 'single_supplier',
        offers: [offer({ counterparty_id: 11, supplier_name: 'ТОО «Столы»' })],
    };
    assert.deepEqual(purchaseIssues(base).map((item) => item.key), [], 'обычная отправка выбором не стопорится');
    assert.deepEqual(purchaseIssues(base, { contractMissing: true }).map((item) => [item.key, item.text]),
        [['contract_id', CONTRACT_ISSUE]]);
    // IT-закуп: способ оплаты выбрал Helpdesk IT, договор при ответе бухгалтерии — инициатора.
    assert.deepEqual(purchaseIssues({ ...base, payment_method: null }, { helpdesk: true, contractMissing: true })
        .map((item) => item.key), ['contract_id']);
    // Регулярный платёж: договор задаёт справочник — пункт стоит у выбора платежа.
    const regular = { fixed_template_id: 7, department_id: 1, payment_period: 'декабрь', amount: 500000,
        invoice_number: 'А-1', invoice_date: '2026-11-10' };
    const template = { counterparty_id: 11, legal_entity_id: 2, object_type: 'service' };
    const keys = regularIssues(regular, { template, fileKinds: ['invoice'], contractMissing: true }).map((item) => item.key);
    assert.deepEqual(keys, ['fixed_template_id']);
    assert.deepEqual(regularIssues(regular, { template, fileKinds: ['invoice'] }), []);
});

test('подсказка поля называет «Без договора», история пишет «Договор»', () => {
    for (const key of ['contract', 'templateContract', 'helpdeskContract']) {
        assert.ok(HINTS[key].options.some(([name]) => name === NO_CONTRACT_LABEL), key);
        assert.match(HINTS[key].outro, /300 000/);
    }
    assert.match(HINTS.contractStatus.intro, /«Без договора»/);
    // Helpdesk IT: заявка вернётся не к нему, а к инициатору.
    assert.match(HINTS.helpdeskContract.outro, /вернётся инициатору/);
    assert.match(HINTS.contract.outro, /вернётся к вам/);
    assert.match(HINTS.contractPending.intro, /только с действующим договором или с отметкой «Без договора»/);
    assert.match(HINTS.regularContract.intro, /«Регулярные платежи»/);
    assert.equal(describeEvent({ kind: 'edited', payload: { changes: { contract_id: [3, null], no_contract: [false, true] } } }),
        'Изменено: Договор');
    assert.equal(describeEvent({ kind: 'edited', payload: { changes: { no_contract: [false, true] } } }), 'Изменено: Договор');
});

/* ── Окно бухгалтера (п. 8) ────────────────────────────────────────────────── */

let stageModule = null;
async function stageForm() {
    if (stageModule) return stageModule;
    const stubPath = join(OUT_DIR, 'axios-stub.mjs');
    writeFileSync(stubPath, 'const never = () => new Promise(() => {});\nexport default { get: never, post: never, patch: never, delete: never };\n');
    const result = await esbuild.build({
        entryPoints: [join(ROOT, 'src/components/payments/PaymentStageForm.jsx')],
        bundle: true, write: false, format: 'esm', platform: 'node', target: 'node18', jsx: 'transform',
        loader: { '.jsx': 'jsx', '.js': 'jsx', '.css': 'empty' },
        external: ['react', 'react-dom', 'lucide-react'], alias: { axios: stubPath }, logLevel: 'silent',
    });
    const outPath = join(OUT_DIR, 'stage.mjs');
    writeFileSync(outPath, result.outputFiles[0].text);
    stageModule = await import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
    return stageModule;
}

const invoiceStage = async (request, check = { applies: true, ok: true, reason: null, threshold: CONTRACT_THRESHOLD }) => {
    const { default: PaymentStageForm } = await stageForm();
    return renderToStaticMarkup(React.createElement(PaymentStageForm, {
        dictionaries: { counterparties: [] }, users: [], busy: false, meta: {}, permissions: {},
        apiBaseUrl: '', headers: () => ({}),
        onAct: () => {}, onTake: () => {}, onEdit: () => {}, onReply: () => {}, onAttach: () => {}, loadCard: () => {},
        data: {
            request: {
                id: 5, request_kind: 'purchase', payment_method: 'invoice', counterparty_id: 11, counterparty_name: 'ТОО «Столы»',
                counterparty_vat: false, invoice_number: 'СЧ-77', invoice_date: '2026-11-10', initiator_name: 'Инициаторова Ирина',
                ...request,
            },
            attachments: [], supplier_history: [], duplicates: [], contract_check: check, clarify_reasons: [],
        },
        subtask: { kind: 'invoice_payment', status: 'checking' },
    }));
};
const text = (html) => html.replace(/<[^>]+>/g, '');

test('бухгалтер видит «Без договора», а свыше порога — с пометкой', async () => {
    const over = text(await invoiceStage({ amount: 900000, no_contract: true }));
    assert.ok(over.includes(`Без договора · счёт больше ${OVER}`), over);
    const small = text(await invoiceStage({ amount: 120000, no_contract: true }));
    assert.ok(small.includes('Без договора') && !small.includes('счёт больше'), 'до порога — без пометки');
    const contract = text(await invoiceStage({ amount: 900000, contract_number: '12', contract_id: 3 }));
    assert.ok(contract.includes('№12') && !contract.includes('Без договора'));
    // Ничего не выбрано (до порога это можно) — «не указан», а не «без договора», как раньше.
    const empty = text(await invoiceStage({ amount: 120000 }));
    assert.ok(empty.includes('не указан') && !/без договора/i.test(empty.split('Договор')[1] || ''), empty);
});

/* ── Карточка заявки: её видит утвердитель ─────────────────────────────────── */

const SEED = 'const [data, setData] = useState(null);';
let renderCard;
async function card(request) {
    if (!renderCard) {
        const result = await esbuild.build({
            stdin: {
                contents: `
                    import React from 'react';
                    import { renderToStaticMarkup } from 'react-dom/server';
                    import Card from './src/components/payments/PaymentRequestCard.jsx';
                    export const render = (props) => renderToStaticMarkup(React.createElement(Card, props));
                `,
                resolveDir: ROOT, loader: 'jsx',
            },
            bundle: true, write: false, format: 'cjs', platform: 'node', target: 'node18', jsx: 'transform',
            loader: { '.jsx': 'jsx', '.js': 'jsx', '.css': 'empty' },
            logLevel: 'silent',
            plugins: [{
                name: 'seed-card-data',
                setup(build) {
                    build.onResolve({ filter: /^axios$/ }, () => ({ path: 'axios', namespace: 'stub' }));
                    build.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({
                        contents: 'const never = () => new Promise(() => {}); export default { get: never, post: never, patch: never, delete: never };',
                    }));
                    build.onLoad({ filter: /PaymentRequestCard\.jsx$/ }, (args) => {
                        const code = readFileSync(args.path, 'utf8');
                        assert.ok(code.includes(SEED), 'в карточке изменилось начальное состояние data — поправьте подмену');
                        return { contents: code.replace(SEED, 'const [data, setData] = useState(globalThis.__PAYMENT_CARD_DATA__ ?? null);'), loader: 'jsx' };
                    });
                },
            }],
        });
        const module = { exports: {} };
        new Function('module', 'exports', 'require', result.outputFiles[0].text)(module, module.exports, require);
        renderCard = module.exports.render;
    }
    globalThis.__PAYMENT_CARD_DATA__ = {
        request: {
            id: 5, number: 5, status: 'active', stage: 'approval', request_kind: 'purchase', expense_name: 'Столы',
            payment_method: 'invoice', amount: 900000, due_on: '2026-11-30', initiator_id: 10, initiator_name: 'Инициатор',
            submitted_at: '2026-11-10T10:00:00', ...request,
        },
        items: [], offers: [], subtasks: [], lifecycle: [], attachments: [], events: [], history: [], duplicates: [],
        assets: [], clarify_reasons: [], submit_problems: [], permissions: {}, closing: [],
        contract_check: { applies: true, ok: Boolean(request.no_contract || request.contract_number), threshold: CONTRACT_THRESHOLD },
    };
    try {
        return text(renderCard({
            apiBaseUrl: 'http://api.test', headers: () => ({}), dictionaries: {}, users: [], me: { id: 1 }, meta: {},
            showToast: () => {}, onClose: () => {}, open: true, requestId: 5,
        }));
    } finally {
        delete globalThis.__PAYMENT_CARD_DATA__;
    }
}

test('карточка: «Без договора» с пометкой; пустое поле свыше порога — «не выбран», пока счёт впереди', async () => {
    assert.ok((await card({ no_contract: true })).includes(`Без договора · счёт больше ${OVER}`));
    assert.ok((await card({ contract_number: '12', contract_id: 3 })).includes('№12'));
    // Коротко, без пересказа правила (оно под «i»): строка подписи «Договор» и сразу значение.
    const pending = /Договорне выбран/;
    const empty = await card({});
    assert.match(empty, pending);
    assert.doesNotMatch(empty, /уйдёт в оплату с договором/, 'пояснение — под «i», не строкой');
    // Счёт уже оплачен, сумма до порога, карта, отменена — предупреждать не о чем.
    assert.doesNotMatch(await card({ paid_on: '2026-11-11', paid_amount: 900000 }), pending);
    assert.doesNotMatch(await card({ amount: 120000 }), pending);
    assert.doesNotMatch(await card({ payment_method: 'card', card_recipient: 'employee' }), pending);
    assert.doesNotMatch(await card({ status: 'cancelled' }), pending);
});

/* ── Форма заявки и справочник (по исходнику) ──────────────────────────────── */

test('форма: «Договор» при оплате счёта — всегда, с пунктом «Без договора», и уходит на сервер', () => {
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    assert.ok(form.includes('no_contract: Boolean(request.no_contract),') && form.includes('no_contract_for: chosenFor,'), 'черновик и заявка');
    // Выбор — общими правилами paymentsMeta (проверены выше), а не своей копией.
    assert.ok(form.includes('const { contractId, noContract } = contractChoice(draft, contractOptions, supplierKey);'));
    assert.ok(form.includes('const pickContract = (value) => setDraft((prev) => ({ ...prev, ...contractPick(value, supplierKey) }));'));
    assert.ok(form.includes('const contractChoices = useMemo(() => [{ value: NO_CONTRACT, label: NO_CONTRACT_LABEL }, ...contractOptions], [contractOptions]);'));
    assert.ok(form.includes('anchor="contract_id" issue={issueOf(\'contract_id\')}'), 'недочёт — красным у поля');
    // Поле больше не прячется, когда у поставщика нет договоров в справочнике.
    const invoice = form.slice(form.indexOf("{draft.payment_method === 'invoice' && (\n                                <>"), form.indexOf('{invoiceOpen ? ('));
    assert.ok(invoice.includes('{contractField}') && !invoice.includes('contractOptions.length > 0'), invoice);
    assert.ok(form.includes("no_contract: draft.payment_method === 'invoice' && noContract,"));
    assert.ok(form.includes('const contractNeeded = draft.payment_method === \'invoice\' && total > threshold && !contractId && !noContract;'));
    // IT-закуп: поле — в блоке поставщиков Helpdesk IT, когда он выбрал оплату счётом.
    const helpdeskBlock = form.slice(form.indexOf('<div data-form-helpdesk'), form.indexOf(') : (<>', form.indexOf('<div data-form-helpdesk')));
    assert.ok(helpdeskBlock.includes('{itInvoice && (') && helpdeskBlock.includes('{contractField}'));
    assert.ok(!form.includes('<IosSection title="Оплата счёта">'), 'отдельного раздела из одного поля нет');
    // Регулярный платёж: выбор из справочника — строкой сводки, пояснение под «i».
    assert.ok(form.includes('template.contract_number ? `№${template.contract_number}` : template.no_contract ? NO_CONTRACT_LABEL : ('));
    assert.ok(form.includes('<FieldHint hint={HINTS.regularContract} />'));
    assert.ok(form.includes("request?.clarify_from === 'invoice_payment'"), 'ответ бухгалтерии — по подзадаче, которая спросила');
});

test('Helpdesk IT выбирает договор вместе с оплатой счётом', () => {
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    assert.ok(stage.includes('no_contract: Boolean(request.no_contract),') && stage.includes('no_contract_for: chosenFor,'));
    assert.ok(stage.includes('const { contractId, noContract } = contractChoice(draft, contractOptions, supplierKey);'));
    assert.ok(stage.includes('onChange={(value) => setDraft((prev) => ({ ...prev, ...contractPick(value, supplierKey) }))}'));
    assert.ok(stage.includes("...(draft.payment_method === 'invoice' ? { contract_id: contractId, no_contract: noContract } : {}),"));
    assert.ok(stage.includes('options={[{ value: NO_CONTRACT, label: NO_CONTRACT_LABEL }, ...contractOptions]}'));
    assert.ok(stage.includes('hint={HINTS.helpdeskContract}'));
});

test('справочник «Регулярные платежи»: «Без договора» — выбор, пустое — «Не выбран»', () => {
    const panel = source('src/components/payments/FixedPaymentsPanel.jsx');
    assert.ok(panel.includes('[{ value: NO_CONTRACT, label: NO_CONTRACT_LABEL }, ...(dictionaries?.contracts || [])'));
    assert.ok(!panel.includes("{ value: '', label: 'Без договора' }"), 'пустое значение больше не называется «Без договора»');
    assert.ok(panel.includes("placeholder={draft.counterparty_id ? 'Не выбран' : 'Сначала поставщик'}"));
    assert.ok(panel.includes('<Field label="Договор" hint={HINTS.templateContract}>'));
    assert.ok(panel.includes('no_contract: Boolean(row.no_contract) && !row.contract_id,'));
    assert.ok(panel.includes("counterparty_id: v || null, contract_id: null, no_contract: false, counterparty_account_id: null"));
});
