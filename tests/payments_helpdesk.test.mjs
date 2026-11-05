/*
 * «Оплата счетов»: IT-закуп через Helpdesk IT (просьба владельца 04.11.2026).
 *
 * «Человек создаёт заявку; если закуп относится к IT — запрос поступает руководителю на
 * согласование, далее в Helpdesk IT: они проверяют, есть ли такое у них, какое имущество сейчас
 * числится за сотрудником, и прикрепляют варианты поставщиков». Выбор владельца: IT-закуп — по
 * категории (отметка «Helpdesk IT»); нашлось в наличии — выдают, заявка закрывается «Выдано из
 * наличия»; цены ставит Helpdesk IT по рекомендуемому поставщику.
 *
 * Здесь — то, что видно без браузера: правила формы Helpdesk IT (те же, что у сервера —
 * workflow.missing_for_action), что форма инициатора IT-закупа не требует поставщиков и цены,
 * подписи, и что форма этапа рисует на каждой ступени. Нажатия и ход заявки по этапам снимают
 * проходы стенда, правила сервера — tests/test_payments_helpdesk*.py.
 *
 * Запуск: node --test tests/payments_helpdesk.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
    CURRENT_KIND_LABELS, HELPDESK_OPTIONS, HINTS, IT_SUPPLIER_CARD, MEMBER_ROLES, STOCK_OPTIONS, UNSTAFFED, amountLabel,
    describeEvent, fmtMoney, helpdeskIssues, issuedFromStock, itPriceMissing, itemsTotal, pricedByOffer, purchaseIssues,
    recommendedOffer, routeSummary, supplierCardBlocked,
} from '../src/components/payments/paymentsMeta.js';
import { PAYMENTS_BOARD_CODES } from '../src/components/payments/paymentsLink.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешние react и lucide-react не резолвятся.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-helpdesk-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

let stageModule = null;
async function stageForm() {
    if (stageModule) return stageModule;
    const stubPath = join(OUT_DIR, 'axios-stub.mjs');
    writeFileSync(stubPath, 'const never = () => new Promise(() => {});\nexport default { get: never, post: never, patch: never, delete: never };\n');
    const result = await esbuild.build({
        entryPoints: [new URL('src/components/payments/PaymentStageForm.jsx', ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
        bundle: true, write: false, format: 'esm', platform: 'node', target: 'node18', jsx: 'transform',
        loader: { '.jsx': 'jsx', '.js': 'jsx', '.css': 'empty' },
        external: ['react', 'react-dom', 'lucide-react'], alias: { axios: stubPath }, logLevel: 'silent',
    });
    const outPath = join(OUT_DIR, 'stage.mjs');
    writeFileSync(outPath, result.outputFiles[0].text);
    stageModule = await import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
    return stageModule;
}

const item = (key, name, quantity, price) => ({ key, name, quantity: String(quantity), unit: 'шт', unit_price: price });
const offer = (key, name, amount, recommended = false) => ({
    key, id: null, counterparty_id: null, supplier_name: name, custom: true, amount, terms: '', link: '', comment: '',
    is_recommended: recommended, file: null, extras: [],
});
const THREE = [offer('a', 'Sulpak', 450000, true), offer('b', 'Technodom', 470000), offer('c', 'Alser', 455000)];

/* ── Форма Helpdesk IT: чего не хватает ──────────────────────────────────────── */

test('пока не отмечено «Есть в наличии?», дальше выбора форма не идёт', () => {
    assert.deepEqual(helpdeskIssues({ offers: [], items: [] }, { inStock: null }).map((issue) => issue.key), ['in_stock']);
});

test('«Есть в наличии»: что выдают — строкой склада или словами в комментарии', () => {
    const empty = helpdeskIssues({ comment: '' }, { inStock: true });
    assert.deepEqual(empty.map((issue) => [issue.key, issue.text]), [['stock_ids', 'выберите, что выдаёте из наличия, или напишите об этом в комментарии']]);
    assert.deepEqual(helpdeskIssues({ stock_ids: [5] }, { inStock: true }), []);
    assert.deepEqual(helpdeskIssues({ comment: 'Выдал ноутбук HP' }, { inStock: true }), []);
});

test('«Нет в наличии»: поставщики по правилам инициатора, цены — по рекомендуемому', () => {
    const draft = { offers: THREE, items: [item('x', 'Ноутбук', 1, 0)], supplier_choice_reason: 'Дешевле' };
    assert.deepEqual(helpdeskIssues(draft, { inStock: false }).map((issue) => [issue.key, issue.part]), [['items', 'price']]);
    const wrong = helpdeskIssues({ ...draft, items: [item('x', 'Ноутбук', 1, 300000)] }, { inStock: false });
    assert.equal(wrong.length, 1);
    assert.equal(wrong[0].text, `сумма позиций — ${fmtMoney(300000)}, а у рекомендуемого поставщика — ${fmtMoney(450000)}: поставьте цены по нему`);
    assert.deepEqual(helpdeskIssues({ ...draft, items: [item('x', 'Ноутбук', 2, 200000), item('y', 'Сумка', 1, 50000)] }, { inStock: false }), []);
    // Вариантов меньше настройки — тот же пункт, что у инициатора.
    const few = helpdeskIssues({ ...draft, offers: THREE.slice(0, 1), items: [item('x', 'Ноутбук', 1, 450000)] }, { inStock: false });
    assert.deepEqual(few.map((issue) => [issue.key, issue.part]), [['offers', 'count']]);
    // «Альтернатив нет»: один поставщик, причина обязательна, рекомендуемый — он сам.
    const alone = { offers: [offer('a', 'Microsoft', 120000)], no_alternatives: true, items: [item('x', 'Лицензия', 1, 120000)] };
    assert.deepEqual(helpdeskIssues(alone, { inStock: false }).map((issue) => issue.key), ['no_alternatives_reason']);
    assert.deepEqual(helpdeskIssues({ ...alone, no_alternatives_reason: 'license_renewal' }, { inStock: false }), []);
    assert.equal(recommendedOffer(alone).supplier_name, 'Microsoft');
});

test('«Поставить цену поставщика»: одна позиция — одна цена или две строки, сумма сходится до тиына', () => {
    const one = [item('x', 'Ноутбук', 2, 0), item('blank', '', 1, 0)];
    const priced = pricedByOffer(one, 900000);
    assert.equal(priced[0].unit_price, 450000);
    assert.equal(priced[1], one[1], 'пустая строка остаётся как была');
    // 3 монитора за 100 000 ₸: 2 × 33 333,33 + 1 × 33 333,34 — ровно 100 000, а не 99 999,99.
    const split = pricedByOffer([item('x', 'Монитор', 3, 0)], 100000);
    assert.deepEqual(split.map((row) => [row.name, row.quantity, row.unit_price]), [['Монитор', '2', 33333.33], ['Монитор', '1', 33333.34]]);
    assert.equal(itemsTotal(split), 100000);
    assert.equal(new Set(split.map((row) => row.key)).size, 2, 'у строк разные ключи');
    assert.equal(pricedByOffer([item('x', 'Кабель', '2,5', 0)], 100.01), null, 'дробное количество, на которое не делится, — руками');
    assert.equal(pricedByOffer([item('x', 'Ноутбук', 1, 0), item('y', 'Мышь', 1, 0)], 450000), null);
    assert.equal(pricedByOffer([item('x', 'Ноутбук', 1, 0)], 0), null);
});

/* ── Форма инициатора IT-закупа ──────────────────────────────────────────────── */

const DRAFT = {
    department_id: 1, expense_name: 'Ноутбук для работы', category_id: 3, justification: 'Старый не тянет 1С',
    items: [item('x', 'Ноутбук', 1, 0)], due_on: '2026-11-20', object_type: 'goods', accounting_category: 'consumable',
    payment_method: 'invoice', offers: [], no_alternatives: false,
};

test('IT-закуп: карту поставщика до выбора поставщика не указать — пункт у «Получателя»', () => {
    const draft = { ...DRAFT, payment_method: 'card', card_recipient: 'supplier' };
    assert.equal(supplierCardBlocked(draft, { helpdesk: true }), true);
    assert.equal(supplierCardBlocked(draft, { helpdesk: true, supplierKnown: true }), false, 'Helpdesk уже выбрал поставщика');
    assert.equal(supplierCardBlocked(draft, { helpdesk: false }), false);
    assert.equal(supplierCardBlocked({ ...draft, card_recipient: 'employee' }, { helpdesk: true }), false);
    assert.deepEqual(purchaseIssues(draft, { helpdesk: true }).map((issue) => [issue.key, issue.text]), [['card_recipient', IT_SUPPLIER_CARD]]);
    // Карта сотрудника у IT-закупа — как обычно: владелец, номер и назначение.
    const employee = purchaseIssues({ ...draft, card_recipient: 'employee' }, { helpdesk: true }).map((issue) => issue.key);
    assert.ok(employee.includes('card_holder_user_id') && employee.includes('payment_purpose'), employee.join(','));
});

test('IT-закуп: инициатор не указывает ни поставщиков, ни цену', () => {
    assert.deepEqual(purchaseIssues(DRAFT, { helpdesk: true }), []);
    const plain = purchaseIssues(DRAFT, { helpdesk: false }).map((issue) => issue.key);
    assert.ok(plain.includes('items') && plain.includes('offers'), plain.join(','));
    // Helpdesk IT не назначен — сказать сразу, у категории: иначе сервер откажет при отправке.
    assert.deepEqual(purchaseIssues(DRAFT, { helpdesk: true, staffed: { helpdesk: false } }).map((issue) => [issue.key, issue.text]),
        [['category_id', UNSTAFFED.helpdesk]]);
    assert.deepEqual(purchaseIssues(DRAFT, { helpdesk: false, staffed: { helpdesk: false } }).filter((issue) => issue.key === 'category_id'), []);
});

/* ── Подписи ─────────────────────────────────────────────────────────────────── */

test('выборы с «i»: подсказка называет ровно варианты селектора, «не выбрано» — не «нет»', () => {
    for (const [hint, options] of [['categoryHelpdesk', HELPDESK_OPTIONS], ['itStock', STOCK_OPTIONS]]) {
        assert.deepEqual(HINTS[hint].options.map(([name]) => name).sort(), options.map((option) => option.label).sort(), hint);
    }
    assert.deepEqual(STOCK_OPTIONS.map((option) => option.value), [false, true]);
    assert.deepEqual(HELPDESK_OPTIONS.map((option) => option.label), ['Не нужен', 'Нужен']);
    assert.ok(HINTS.itemsHelpdesk.outro.includes('Цену можно не указывать'));
});

test('роль, доска, этап и история называются словами', () => {
    const role = MEMBER_ROLES.find((item) => item.code === 'helpdesk');
    assert.equal(role.label, 'Helpdesk IT');
    assert.ok(!role.required, 'нужна только IT-закупам');
    assert.ok(PAYMENTS_BOARD_CODES.includes('helpdesk'));
    assert.equal(CURRENT_KIND_LABELS.it_review, 'Проверка Helpdesk IT');
    assert.equal(describeEvent({ kind: 'it_offers', payload: { recommended: 'Sulpak', amount: 450000 } }),
        `Helpdesk IT подобрал поставщиков: рекомендуемый — Sulpak, ${fmtMoney(450000)}`);
    assert.equal(describeEvent({ kind: 'it_offers', payload: {} }), 'Helpdesk IT подобрал поставщиков');
    assert.equal(describeEvent({ kind: 'it_stock', payload: { assets: [{ name: 'Ноутбук HP' }, { name: 'Мышь' }] } }),
        'Выдано из наличия: Ноутбук HP, Мышь');
    assert.equal(describeEvent({ kind: 'it_stock', payload: {} }), 'Выдано из наличия');
});

test('выдано из наличия — так и сказано в «Ходе заявки»; сумма без цены — прочерк, а не 0 ₸', () => {
    const steps = [{ kind: 'it_review', state: 'done', outcome: 'from_stock' }];
    assert.equal(issuedFromStock(steps), true);
    assert.equal(issuedFromStock([{ kind: 'it_review', state: 'done', outcome: 'offers' }]), false);
    assert.equal(routeSummary({ status: 'done' }, steps), 'Выдано из наличия');
    assert.equal(routeSummary({ status: 'done' }, []), 'Все этапы пройдены');
    assert.equal(itPriceMissing({ amount: 0, it_purchase: true }), true);
    assert.equal(amountLabel({ amount: 0, it_purchase: true }), '—');
    assert.equal(amountLabel({ amount: 0, category_helpdesk: true }), '—');
    assert.equal(amountLabel({ amount: 0 }), fmtMoney(0), 'обычная заявка без суммы — как была');
    assert.equal(amountLabel({ amount: 450000, it_purchase: true }), fmtMoney(450000));
    assert.equal(amountLabel({ amount: 450000, refund_amount: 50000, it_purchase: true }), fmtMoney(400000));
});

/* ── Форма этапа на ступенях ─────────────────────────────────────────────────── */

const helpdeskHtml = async (status, extra = {}) => {
    const { default: PaymentStageForm } = await stageForm();
    return renderToStaticMarkup(React.createElement(PaymentStageForm, {
        dictionaries: { settings: { min_suppliers: 3 }, counterparties: [] }, users: [], busy: false,
        meta: { no_alternatives_reasons: [{ code: 'single_supplier', label: 'Единственный поставщик' }] },
        permissions: {}, apiBaseUrl: '', headers: () => ({}),
        onAct: () => {}, onEdit: () => {}, onReply: () => {}, onAttach: () => {}, loadCard: () => {},
        data: {
            request: { id: 1, amount: 0, request_kind: 'purchase', object_type: 'goods', initiator_name: 'Инициаторова Ирина', it_purchase: true },
            attachments: [], offers: [], items: [{ id: 1, name: 'Ноутбук', quantity: '1.000', unit: 'шт', unit_price: 0 }],
            initiator_assets: [{ id: 3, name: 'Монитор Dell', inventory_number: 'IT-0007', category_name: 'Мониторы', status: 'in_use' }],
            ...extra,
        },
        subtask: { kind: 'it_review', status, brief: 'Проверьте, нет ли такого в наличии' },
    }));
};

test('ступень «Новые»: под рукой имущество сотрудника и склад, решения ещё нет', async () => {
    const html = await helpdeskHtml('new');
    assert.match(html, /Проверьте, нет ли такого в наличии/);
    assert.match(html, /Имущество сотрудника[\s\S]*Монитор Dell[\s\S]*инв\. IT-0007/);
    assert.match(html, /aria-label="Поиск по складу"/);
    assert.doesNotMatch(html, /Есть в наличии\?|Передать на утверждение|Выдать из наличия|Вернуть на доработку/);
});

test('ступень «В работе»: выбор «Есть в наличии?» и возврат на доработку; итог — после выбора', async () => {
    const html = await helpdeskHtml('in_progress');
    assert.match(html, /Есть в наличии\?[\s\S]*role="tablist" aria-label="Есть ли нужное в наличии"/);
    assert.match(html, /Вернуть на доработку/);
    assert.doesNotMatch(html, /Передать на утверждение|Выдать из наличия|Варианты поставщиков/, 'пока не выбрано — ни поставщиков, ни выдачи');
    const nobody = await helpdeskHtml('new', { initiator_assets: [] });
    assert.match(nobody, /В реестре имущества за сотрудником ничего не числится/);
    // Имущество сотрудника не положено смотрящему (сервер прислал null) — блока нет вовсе.
    assert.doesNotMatch(await helpdeskHtml('new', { initiator_assets: null }), /Имущество сотрудника/);
});

/* ── Проводка (по исходнику) ─────────────────────────────────────────────────── */

test('форма заявки IT-закупа не шлёт поставщиков: на доработке они подобраны Helpdesk IT', () => {
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    assert.ok(form.includes('...(helpdesk ? {} : {'));
    assert.ok(form.includes("if (!regular && !helpdesk) {"), 'КП к вариантам — тоже нет');
    // Как у сервера: отправленная заявка с прежней категорией — по запомненному it_purchase.
    assert.ok(form.includes("const helpdesk = kind === 'purchase' && !legacy && (sentSameCategory ? Boolean(request?.it_purchase)"));
    assert.ok(form.includes(': Boolean(categories.find((item) => item.id === draft.category_id)?.via_helpdesk));'));
    assert.ok(form.includes('supplierKnown: Boolean(supplierId),'));
    assert.ok(form.includes('if (!open || !kind || !(total > 0 || helpdesk))'), 'маршрут виден и без цены');
});

test('этап, справочник, стол и окно заявки знают про Helpdesk IT', () => {
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    assert.ok(stage.includes("if (kind === 'it_review') {"));
    assert.ok(stage.includes('axios.get(`${apiBaseUrl}/api/payments/stock?${params}`'));
    assert.ok(stage.includes("action: 'it_offers'") && stage.includes("action: 'it_stock'"));
    assert.ok(stage.includes("files: sent.map((offer, index) => (offer.file ? { file: offer.file, kind: 'offer', offerIndex: index } : null)).filter(Boolean)"));
    // Проверка видит то же, что уйдёт: при «альтернатив нет» — один вариант (разбор 04.11.2026).
    assert.ok(stage.includes('helpdeskIssues({ ...draft, offers: offersShown, stock_ids: chosen.map((item) => item.id) },'));
    // Уже выданную по другой заявке вещь не выбрать; не вышла выдача — склад перечитан, выбор снят.
    assert.ok(stage.includes('const pickable = selectable && !item.issued_request_id;'));
    assert.ok(stage.includes('if (ok === false) { setChosen([]); setReload((value) => value + 1); }'));
    const dictionaries = source('src/components/payments/PaymentsDictionaries.jsx');
    assert.ok(dictionaries.includes("via_helpdesk: Boolean(row.via_helpdesk)"));
    assert.ok(dictionaries.includes('<Choice value={Boolean(draft.via_helpdesk)} options={HELPDESK_OPTIONS}'));
    assert.ok(dictionaries.includes('{!draft.parent_id && ('), 'отметка — только у категории верхнего уровня');
    const desk = source('src/components/payments/PaymentsDesk.jsx');
    assert.ok(desk.includes('    it_review: MonitorCheck,\n'));
    const card = source('src/components/payments/PaymentRequestCard.jsx');
    assert.ok(card.includes("it_offers: 'Передано на утверждение', it_stock: 'Выдано из наличия — заявка закрыта',"));
    assert.ok(card.includes('data-card-helpdesk'));
});
