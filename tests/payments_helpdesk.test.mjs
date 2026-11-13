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
    CURRENT_KIND_LABELS, HELPDESK_OPTIONS, HINTS, MEMBER_ROLES, ROLE_LABELS, STOCK_OPTIONS, UNSTAFFED, amountLabel,
    cardIssues, describeEvent, fmtMoney, helpdeskIssues, issuedFromStock, itPriceMissing, itemsTitle, itemsTotal,
    objectIssues, pricedByOffer, purchaseIssues, recommendedOffer, routeSummary,
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

// Товар или услуга, категория учёта и способ оплаты — тоже Helpdesk IT (владелец 06.11.2026).
const PAID = { object_type: 'goods', accounting_category: 'asset', payment_method: 'invoice' };

/* Счёт поставщика у оплаты счётом прикладывает Helpdesk IT (13.11.2026): в проверках ниже он уже приложен —
   его отсутствие проверяет отдельный тест. */
const withInvoice = (draft, options) => helpdeskIssues({ invoice_number: 'СЧ-1', invoice_date: '2026-11-13', ...draft },
    { hasInvoice: true, ...options });

test('«Нет в наличии»: поставщики по правилам инициатора, цены — по рекомендуемому', () => {
    const draft = { ...PAID, offers: THREE, items: [item('x', 'Ноутбук', 1, 0)], supplier_choice_reason: 'Дешевле' };
    assert.deepEqual(withInvoice(draft, { inStock: false }).map((issue) => [issue.key, issue.part]), [['items', 'price']]);
    const wrong = withInvoice({ ...draft, items: [item('x', 'Ноутбук', 1, 300000)] }, { inStock: false });
    assert.equal(wrong.length, 1);
    assert.equal(wrong[0].text, `сумма позиций — ${fmtMoney(300000)}, а у рекомендуемого поставщика — ${fmtMoney(450000)}: поставьте цены по нему`);
    assert.deepEqual(withInvoice({ ...draft, items: [item('x', 'Ноутбук', 2, 200000), item('y', 'Сумка', 1, 50000)] }, { inStock: false }), []);
    // Вариантов меньше настройки — тот же пункт, что у инициатора.
    const few = withInvoice({ ...draft, offers: THREE.slice(0, 1), items: [item('x', 'Ноутбук', 1, 450000)] }, { inStock: false });
    assert.deepEqual(few.map((issue) => [issue.key, issue.part]), [['offers', 'count']]);
    // «Альтернатив нет»: один поставщик, причина обязательна, рекомендуемый — он сам.
    const alone = { ...PAID, offers: [offer('a', 'Microsoft', 120000)], no_alternatives: true, items: [item('x', 'Лицензия', 1, 120000)] };
    assert.deepEqual(withInvoice(alone, { inStock: false }).map((issue) => issue.key), ['no_alternatives_reason']);
    assert.deepEqual(withInvoice({ ...alone, no_alternatives_reason: 'license_renewal' }, { inStock: false }), []);
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

test('«Нет в наличии»: товар или услугу, категорию учёта и способ оплаты выбирает Helpdesk IT', () => {
    const base = { offers: THREE, items: [item('x', 'Ноутбук', 1, 450000)], supplier_choice_reason: 'Дешевле' };
    assert.deepEqual(withInvoice(base, { inStock: false }).map((issue) => issue.key), ['object_type', 'payment_method']);
    assert.deepEqual(withInvoice({ ...base, object_type: 'goods', payment_method: 'invoice' }, { inStock: false })
        .map((issue) => issue.key), ['accounting_category']);
    assert.deepEqual(withInvoice({ ...base, object_type: 'service', payment_method: 'invoice' }, { inStock: false }), []);
    // Карта — как у инициатора: получатель, владелец, номер, назначение.
    const card = { ...base, ...PAID, payment_method: 'card' };
    assert.deepEqual(withInvoice(card, { inStock: false }).map((issue) => issue.key), ['card_recipient', 'card_number', 'payment_purpose']);
    const supplier = { ...card, card_recipient: 'supplier', card_holder_name: 'ИП Алсер', card_number: '4400430212345678', payment_purpose: 'Ноутбук' };
    assert.deepEqual(withInvoice(supplier, { inStock: false }), []);
    // Номер, что уже лежит в заявке, годится (hasCard), — вводить его заново не нужно.
    assert.deepEqual(withInvoice({ ...supplier, card_number: '' }, { inStock: false, hasCard: true }), []);
    assert.deepEqual(withInvoice({ ...supplier, card_number: '' }, { inStock: false }).map((issue) => issue.key), ['card_number']);
    // Те же правила — общими функциями формы заявки и этапа.
    assert.deepEqual(objectIssues({ object_type: 'goods', accounting_category: 'asset' }, { staffed: { asset_keeper: false } })
        .map((issue) => [issue.key, issue.text]), [['accounting_category', UNSTAFFED.asset_keeper]]);
    assert.deepEqual(cardIssues({ payment_method: 'invoice' }), []);
});
test('«Нет в наличии» и оплата счётом: счёт, номер и дату прикладывает Helpdesk IT', () => {
    const draft = { ...PAID, payment_method: 'invoice', object_type: 'service', offers: [offer('a', 'Sulpak', 450000)],
        no_alternatives: true, no_alternatives_reason: 'single_supplier', items: [item('x', 'Ноутбук', 1, 450000)] };
    assert.deepEqual(helpdeskIssues(draft, { inStock: false }).map((issue) => issue.key),
        ['invoice_file', 'invoice_number', 'invoice_date']);
    assert.deepEqual(helpdeskIssues({ ...draft, invoice_number: 'СЧ-9', invoice_date: '2026-11-13' },
        { inStock: false, hasInvoice: true }), []);
    // Карта — счёт не нужен.
    const card = { ...draft, payment_method: 'card', card_recipient: 'supplier', card_holder_name: 'ИП Алсер',
        card_number: '4400430212345678', payment_purpose: 'Ноутбук' };
    assert.deepEqual(helpdeskIssues(card, { inStock: false }), []);
});


/* ── Форма заявки: инициатор IT-закупа ───────────────────────────────────────── */

test('IT-закуп: инициатор пишет что, сколько, срок — без названия, обоснования, поставщиков, цены и оплаты', () => {
    const minimal = { department_id: 3, category_id: 5, due_on: '2026-11-20', items: [item('x', 'Мышка', 1, 0)] };
    assert.deepEqual(purchaseIssues(minimal, { helpdesk: true }), []);
    // Обычный закуп с тем же — не хватает всего остального.
    const plain = purchaseIssues(minimal, { helpdesk: false }).map((issue) => issue.key);
    for (const key of ['expense_name', 'justification', 'items', 'object_type', 'payment_method', 'offers']) assert.ok(plain.includes(key), key);
    // Чего не хватает и IT-закупу: категория, позиция, срок, подразделение.
    assert.deepEqual(purchaseIssues({ items: [] }, { helpdesk: true }).map((issue) => issue.key), ['department_id', 'category_id', 'items', 'due_on']);
    // Роль «Helpdesk IT и учёт имущества» пуста — сказать сразу, у категории: иначе сервер откажет при отправке.
    assert.deepEqual(purchaseIssues(minimal, { helpdesk: true, staffed: { asset_keeper: false } }).map((issue) => [issue.key, issue.text]),
        [['category_id', UNSTAFFED.it_review]]);
    assert.deepEqual(purchaseIssues(minimal, { helpdesk: false, staffed: { asset_keeper: false } }).filter((issue) => issue.key === 'category_id'), []);
});

test('название IT-закупа — из позиций: «Мышка», «Мышка и ещё 2»', () => {
    assert.equal(itemsTitle([item('x', 'Мышка', 1, 0)]), 'Мышка');
    assert.equal(itemsTitle([item('x', ' Мышка ', 1, 0), item('b', '', 1, 0), item('y', 'Коврик', 1, 0), item('z', 'Клавиатура', 1, 0)]), 'Мышка и ещё 2');
    assert.equal(itemsTitle([]), '');
    assert.equal(itemsTitle([item('x', 'М'.repeat(400), 1, 0)]).length, 300, 'длина — как у поля наименования');
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
    // Helpdesk IT и учёт имущества — один человек (владелец 06.11.2026): одна роль, отдельной нет.
    assert.equal(MEMBER_ROLES.find((item) => item.code === 'helpdesk'), undefined);
    const role = MEMBER_ROLES.find((item) => item.code === 'asset_keeper');
    assert.equal(role.label, 'Helpdesk IT и учёт имущества');
    assert.equal(ROLE_LABELS.asset_keeper, 'Helpdesk IT и учёт имущества');
    assert.ok(!role.required, 'нужна только IT-закупам и имуществу');
    assert.deepEqual(role.hint.options.map(([name]) => name), ['IT-закуп', 'Имущество']);
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

test('«Новые»: имущество сотрудника, склад и решение — сразу, без «Взять в работу»', async () => {
    // Владелец 06.11.2026 со снимком «Новых»: «в данном окне я не могу ничего выбрать».
    const html = await helpdeskHtml('new');
    assert.match(html, /Проверьте, нет ли такого в наличии/);
    assert.match(html, /Имущество сотрудника[\s\S]*Монитор Dell[\s\S]*инв\. IT-0007/);
    assert.match(html, /aria-label="Поиск по складу"/);
    assert.match(html, /Есть в наличии\?[\s\S]*role="tablist" aria-label="Есть ли нужное в наличии"/);
    assert.match(html, /Вернуть на доработку/);
});

test('после выбора: строки склада нажимаются сразу, первый выбор ставит карточку в «В работе»', () => {
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    assert.ok(stage.includes("const take = () => { if (subtask.status === 'new') onTake?.(); };"));
    assert.ok(stage.includes('const pickable = !item.issued_request_id;'), 'строка склада — сразу, без «Есть»');
    const toggle = stage.slice(stage.indexOf('const toggle = (item) => {'), stage.indexOf('const counterpartyOptions'));
    assert.ok(toggle.includes('if (!on && inStock !== true) { setInStock(true);') && toggle.includes('if (!on) take();'));
    const pick = stage.slice(stage.indexOf('const pick = (value) => {'), stage.indexOf('const toggle = (item) => {'));
    assert.ok(pick.includes('if (value === false) setChosen([]);') && pick.includes('take();'));
    assert.ok(source('src/components/payments/PaymentRequestCard.jsx').includes("onTake={() => move('in_progress')}"));
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

test('форма заявки IT-закупа: что, сколько, срок и пожелания — остальное не шлёт', () => {
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    const payload = form.slice(form.indexOf('        if (helpdesk) {'), form.indexOf('        const sent = offers.filter((offer) => !isBlankOffer(offer));'));
    assert.ok(payload.includes('const title = itemsTitle(draft.items);') && payload.includes('...(title ? { expense_name: title } : {})'));
    for (const key of ['offers', 'payment_method', 'object_type', 'accounting_category', 'card_recipient', 'card_number', 'legal_entity_id',
        'invoice_number']) {
        assert.ok(!payload.includes(`${key}:`), `IT-закуп не шлёт ${key}: его выбирает Helpdesk IT`);
    }
    // Договор (10.11.2026) выбирает Helpdesk IT вместе с оплатой счётом, а инициатор — запасным ходом, когда
    // счёт свыше порога вернулся к нему: только при оплате счётом и только договор или «Без договора».
    const contract = payload.match(/contract_id/g) || [];
    assert.equal(contract.length, 1, 'договор — одним местом');
    assert.ok(payload.includes('...(itInvoice ? { contract_id: contractId, no_contract: noContract } : {}),'));
    // Оплату счётом выбрал Helpdesk IT — это записано в заявке; способ из черновика до смены категории не в счёт.
    assert.ok(form.includes("const itInvoice = helpdesk && request?.payment_method === 'invoice';"));
    assert.ok(form.includes("if (!regular && !helpdesk) {"), 'КП к вариантам — тоже нет');
    // Как у сервера: отправленная заявка с прежней категорией — по запомненному it_purchase.
    assert.ok(form.includes("const helpdesk = kind === 'purchase' && !legacy && (sentSameCategory ? Boolean(request?.it_purchase)"));
    assert.ok(form.includes(': Boolean(categories.find((item) => item.id === draft.category_id)?.via_helpdesk));'));
    assert.ok(form.includes('if (!open || !kind || !(total > 0 || helpdesk))'), 'маршрут виден и без цены');
    // На экране: категория — первой; у IT-закупа нет названия, обоснования, типа объекта, способа оплаты.
    const what = form.slice(form.indexOf('<IosSection title="Что закупаем">'), form.indexOf('<IosSection title="Для кого">'));
    assert.ok(what.indexOf('label="Категория закупа"') < what.indexOf('label="Наименование закупа"'));
    assert.ok(what.includes('{!helpdesk && (\n                                <Field label="Наименование закупа"'));
    assert.ok(what.includes('<Field label="Пожелания" hint={HINTS.wishes} anchor="justification">'));
    assert.ok(form.includes('{!helpdesk && (\n                        <IosSection title="Способ оплаты">'));
    assert.ok(form.includes('{(!helpdesk || !draft.department_id) && ('), '«Для кого» у IT — только без подразделения');
});

test('этап, справочник, стол и окно заявки знают про Helpdesk IT', () => {
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    assert.ok(stage.includes("if (kind === 'it_review') {"));
    assert.ok(stage.includes('axios.get(`${apiBaseUrl}/api/payments/stock?${params}`'));
    assert.ok(stage.includes("action: 'it_offers'") && stage.includes("action: 'it_stock'"));
    assert.ok(stage.includes("...sent.map((offer, index) => (offer.file ? { file: offer.file, kind: 'offer', offerIndex: index } : null)).filter(Boolean),"));
    // Счёт поставщика — от Helpdesk IT вместе с поставщиком (13.11.2026): файл, номер и дата уходят с действием.
    assert.ok(stage.includes("...(draft.payment_method === 'invoice' ? invoiceFiles.map((item) => ({ file: item.file, kind: 'invoice' })) : []),"));
    assert.ok(stage.includes("invoice_number: draft.invoice_number.trim(), invoice_date: draft.invoice_date || null,"));
    for (const anchor of ['invoice_file', 'invoice_number', 'invoice_date']) {
        assert.ok(stage.includes(`anchor="${anchor}" issue={issueOf('${anchor}')}`), anchor);
    }
    assert.ok(!stage.includes('Счёт приложат после утверждения'), 'старая подпись «счёт попросят перед оплатой» убрана');
    // Проверка видит то же, что уйдёт: при «альтернатив нет» — один вариант (разбор 04.11.2026).
    assert.ok(stage.includes('helpdeskIssues({ ...draft, offers: offersShown, stock_ids: chosen.map((item) => item.id) },'));
    // Товар или услуга, учёт и способ оплаты — на этапе Helpdesk IT (06.11.2026); карта — общим блоком с формой заявки.
    for (const anchor of ['object_type', 'accounting_category', 'payment_method']) {
        assert.ok(stage.includes(`anchor="${anchor}" issue={issueOf('${anchor}')}`), anchor);
    }
    assert.ok(stage.includes('<PaymentCardFields') && source('src/components/payments/PaymentRequestForm.jsx').includes('<PaymentCardFields'));
    assert.ok(stage.includes("...(cardKept ? {} : (draft.card_id ? { card_id: draft.card_id } : { card_number: cardDigits(draft.card_number) })),"));
    // «Передать на утверждение» шлёт выбор Helpdesk: товар или услуга, категория учёта (только у товара), способ оплаты.
    const pass = stage.slice(stage.indexOf('const passOffers = () => {'), stage.indexOf('const issueStock = async () => {'));
    for (const line of ['object_type: draft.object_type,', "accounting_category: draft.object_type === 'goods' ? draft.accounting_category : null,",
        'payment_method: draft.payment_method,']) {
        assert.ok(pass.includes(line), line);
    }
    // Уже выданную по другой заявке вещь не выбрать; не вышла выдача — склад перечитан, выбор снят.
    assert.ok(stage.includes('const pickable = !item.issued_request_id;'));
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
    // До решения Helpdesk тип объекта и способ оплаты — «выберет Helpdesk IT»; у IT-закупа — «Пожелания».
    assert.equal((card.match(/itPending \? <span className="text-slate-500">выберет Helpdesk IT<\/span> : null/g) || []).length, 2);
    assert.ok(card.includes("<Row label={itRequest ? 'Пожелания' : 'Описание и обоснование'} wide>{request.justification}</Row>"));
});
