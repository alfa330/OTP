/*
 * «Оплата счетов»: проводка «чего не хватает — красным у поля» (дополнение к payments_issues.test.mjs).
 *
 * Там — правила и сторожа строк исходника. Здесь — то, что мутационный прогон 14.10.2026 менял
 * без единого красного теста: какое поле строки позиций и поставщиков краснеет по пункту, якоря
 * карточек имущества, рамки у даты и выбора сотрудника, стрелки IssueNav, стрелки этапа до первой
 * попытки, подмена ключей в форме заявки и пункты панели причины.
 *
 * Запуск: node --test tests/payments_issues_wiring.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import { issueStep, legacyIssues } from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-issues-wiring-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

const bundles = new Map();
async function bundle(entry, name) {
    if (bundles.has(name)) return bundles.get(name);
    const stubPath = join(OUT_DIR, 'axios-stub.mjs');
    writeFileSync(stubPath, 'const never = () => new Promise(() => {});\nexport default { get: never, post: never, patch: never, delete: never };\n');
    const result = await esbuild.build({
        entryPoints: [new URL(entry, ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
        bundle: true, write: false, format: 'esm', platform: 'node', target: 'node18', jsx: 'transform',
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
const noop = () => {};
const RING = 'ring-rose-300';
/* Тег (input / button) с данной подписью для читалки — в красной ли он рамке. */
const tagsWith = (html, tag, marker) => html.split(`<${tag}`).slice(1).map((chunk) => chunk.slice(0, chunk.indexOf('>') + 1))
    .filter((open) => open.includes(marker));
const redCount = (html, tag, marker) => tagsWith(html, tag, marker).filter((open) => open.includes(RING)).length;
/* Кусок исходника между двумя метками (обе должны найтись). */
const between = (text, from, to) => {
    const start = text.indexOf(from);
    assert.ok(start >= 0, `нет «${from}»`);
    const end = text.indexOf(to, start + from.length);
    assert.ok(end >= 0, `нет «${to}» после «${from}»`);
    return text.slice(start, end);
};

/* ── стрелка: поле, которое исправили, ушло с экрана ─────────────────────────── */

test('стрелка вверх от поля, которого больше нет на экране, — на последнее, вниз — на первое', () => {
    // «Сотрудник» исчез, когда получателем выбрали поставщика: места на экране у него нет.
    assert.equal(issueStep(['b', 'd'], 'gone', -1, ['a', 'b', 'c', 'd']), 'd');
    assert.equal(issueStep(['b', 'd'], 'gone', 1, ['a', 'b', 'c', 'd']), 'b');
});

/* ── позиции: красится то поле строки, о котором пункт ───────────────────────── */

test('позиции: пустой список — название первой строки; «цена» — цены строк из пункта; первая версия — цены всех начатых', async () => {
    const { default: PaymentItemsEditor } = await bundle('src/components/payments/PaymentItemsEditor.jsx', 'items');
    const render = (items, issue) => renderToStaticMarkup(h(PaymentItemsEditor, { items, onChange: noop, issue }));
    const blank = { key: 'a', name: '', quantity: '1', unit: 'шт', unit_price: 0 };
    const empty = render([blank], { key: 'items', part: 'empty' });
    assert.equal(redCount(empty, 'input', 'aria-label="Товар или услуга"'), 1, 'пусто — краснеет название');
    assert.equal(redCount(empty, 'input', 'aria-label="Цена за единицу"'), 0);

    const named = [
        { key: 'a', name: 'Бумага', quantity: '2', unit: 'шт', unit_price: 0 },
        { key: 'b', name: 'Ручки', quantity: '1', unit: 'шт', unit_price: 0 },
    ];
    const price = render(named, { key: 'items', part: 'price', rows: ['a', 'b'] });
    assert.equal(redCount(price, 'input', 'aria-label="Цена за единицу"'), 2, 'у пункта без field — цена');
    assert.equal(redCount(price, 'input', 'aria-label="Товар или услуга"'), 0);

    // Заявка первой версии: пункт «укажите сумму» без rows — цены у всех начатых строк.
    const [legacy] = legacyIssues({ request_kind: 'purchase', legal_entity_id: 1, payment_method: 'card', items: named });
    assert.equal(legacy.part, 'price');
    assert.equal(redCount(render(named, legacy), 'input', 'aria-label="Цена за единицу"'), 2);
    // Без пункта (до попытки) ничего не красное.
    assert.doesNotMatch(render(named, null), new RegExp(RING));
});

/* ── поставщики: «альтернатив нет» и «отметьте рекомендуемого» ─────────────────── */

test('поставщики: пустой единственный поставщик краснеет; «Рекомендуемый» — только у начатых вариантов', async () => {
    const { default: PaymentOffersEditor, newOffer } = await bundle('src/components/payments/PaymentOffersEditor.jsx', 'offers');
    const single = renderToStaticMarkup(h(PaymentOffersEditor, {
        offers: [newOffer({ key: 'o1' })], onChange: noop, single: true, issues: [{ key: 'offers', part: 'empty' }],
    }));
    assert.equal(redCount(single, 'input', 'aria-label="Поставщик 1: стоимость"'), 1, 'стоимость единственного');
    assert.equal(redCount(single, 'button', 'aria-label="Поставщик 1"'), 1, 'выбор поставщика');

    const offers = [
        newOffer({ key: 'o1', counterparty_id: 11, amount: 100 }),
        newOffer({ key: 'o2', counterparty_id: 12, amount: 200 }),
        newOffer({ key: 'o3' }),
    ];
    const recommend = renderToStaticMarkup(h(PaymentOffersEditor, {
        offers, onChange: noop, counterpartyOptions: [{ value: 11, label: 'А' }, { value: 12, label: 'Б' }],
        issues: [{ key: 'offers', part: 'recommend' }],
    }));
    assert.equal(redCount(recommend, 'button', 'title="У этого поставщика предлагаю купить"'), 2);
    assert.equal(redCount(recommend, 'input', 'стоимость"'), 0, 'стоимости заполнены — не красные');
});

/* ── имущество: у каждого пункта assetIssues есть своё поле в карточке ──────────── */

test('имущество: каждый ключ assetIssues — якорь своего поля, пункт рисуется под ним', async () => {
    const { default: PaymentAssetsEditor, assetIssues } = await bundle('src/components/payments/PaymentAssetsEditor.jsx', 'assets');
    const asset = {
        key: 'e', name: '', category_id: null, serial_number: '', inventory_number: '', received_on: '', cost: 0,
        legal_entity_id: null, city: null, department_id: null, responsible_user_id: null, location: '', status: null,
    };
    const issues = assetIssues([asset]);
    assert.equal(issues.length, 11);
    const texts = Object.fromEntries(issues.map((item) => [item.key, item.text]));
    const html = renderToStaticMarkup(h(PaymentAssetsEditor, {
        assets: [asset], onChange: noop, users: [], dictionaries: {}, issueOf: (key) => texts[key] || '',
    }));
    for (const { key, text } of issues) {
        assert.equal(html.split(`data-issue-anchor="${key}"`).length - 1, 1, `одно поле с якорем ${key}`);
        assert.ok(html.includes(`data-issue-anchor="${key}" data-issue="true"`), `${key} в красном`);
        assert.ok(html.includes(`data-issue-text="true">${text[0].toUpperCase()}`) || html.includes(text), `${key}: строка «${text}»`);
    }
    // Без пунктов — карточка спокойная.
    assert.doesNotMatch(renderToStaticMarkup(h(PaymentAssetsEditor, { assets: [asset], onChange: noop, users: [], dictionaries: {} })), /data-issue="true"|ring-rose-300/);
});

/* ── дата и выбор сотрудника внутри поля с пунктом ──────────────────────────────── */

test('поле с пунктом красит и дату, и выбор сотрудника', async () => {
    const { Field, FormDate, UserSelect } = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const render = (issue) => renderToStaticMarkup(h(Field, { label: 'Дата', anchor: 'paid_on', issue },
        h(FormDate, { value: '', onChange: noop, ariaLabel: 'Дата оплаты' }),
        h(UserSelect, { users: [{ id: 1, name: 'Иванова' }], value: null, onChange: noop })));
    assert.equal(render('Укажите дату').split('ring-2 ring-rose-300').length - 1, 2);
    assert.doesNotMatch(render(''), /ring-rose-300/);
});

/* ── стрелки «Не хватает» ──────────────────────────────────────────────────────── */

const hostButtons = (node, found = []) => {
    if (!node || typeof node !== 'object') return found;
    if (Array.isArray(node)) { node.forEach((child) => hostButtons(child, found)); return found; }
    if (node.type === 'button') found.push(node);
    hostButtons(node.props?.children, found);
    return found;
};

test('IssueNav: «вверх» — к предыдущему полю (−1), «вниз» — к следующему (+1)', async () => {
    const { IssueNav } = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const steps = [];
    const buttons = hostButtons(IssueNav({ count: 3, onStep: (step) => steps.push(step) }));
    const byLabel = Object.fromEntries(buttons.map((button) => [button.props['aria-label'], button]));
    byLabel['К предыдущему полю, где не хватает данных'].props.onClick();
    byLabel['К следующему полю, где не хватает данных'].props.onClick();
    assert.deepEqual(steps, [-1, 1]);
});

test('этап: до первой попытки стрелок в панели окна нет, даже если пустых полей несколько', async () => {
    const { default: PaymentStageForm } = await bundle('src/components/payments/PaymentStageForm.jsx', 'stage');
    const props = {
        data: { request: { id: 1, amount: 0, request_kind: 'purchase' }, attachments: [], items: [] },
        subtask: { kind: 'invoice_payment' }, dictionaries: {}, users: [], meta: {}, permissions: {},
        busy: false, onAct: noop, onEdit: noop, onReply: noop, onAttach: noop, loadCard: noop,
        // Слот есть (окно заявки дало место): стрелки рисовались бы порталом — на сервере это падение.
        issueSlot: {},
    };
    const html = renderToStaticMarkup(h(PaymentStageForm, props));
    assert.doesNotMatch(html, /Не хватает|data-issue="true"|ring-rose-300/);
    assert.match(html, /<button type="button" class="[^"]*">(?:<svg[^>]*>.*?<\/svg>)? ?Оплачено<\/button>/s, 'кнопка не disabled');
});

/* ── форма заявки: подмена ключей и проводка (форма — в IosModal, на сервере не рисуется) ── */

test('форма заявки: пункт без поля на экране стоит у поля, которое надо заполнить раньше, и говорит, что сделать там', () => {
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    const body = between(form, 'const placed = [];', 'return placed;');
    const constant = (name) => form.match(new RegExp(`const ${name} = '([^']+)';`))[1];
    const LEGACY = constant('LEGACY_METHOD_ISSUE');
    const ENTITY = constant('TEMPLATE_ENTITY_ISSUE');
    const remap = new Function('list', 'draft', 'regular', 'LEGACY_METHOD_ISSUE', 'TEMPLATE_ENTITY_ISSUE', `${body} return placed;`);
    const run = (keys, draft, regular = false) => remap(keys.map((key) => ({ key, text: key })), draft, regular, LEGACY, ENTITY);
    // Номер карты до выбора получателя не показан: его поле появится вместе с выбором.
    assert.deepEqual(run(['card_recipient', 'card_number'], { card_recipient: null }).map((item) => item.key), ['card_recipient']);
    assert.deepEqual(run(['card_number'], { card_recipient: 'employee' }).map((item) => item.key), ['card_number']);
    // Счёт старой заявки без способа оплаты — одной фразой у «Как платим».
    for (const method of ['card', null]) {
        assert.deepEqual(run(['invoice_file', 'invoice_number', 'invoice_date'], { payment_method: method }),
            [{ key: 'payment_method', text: LEGACY }], String(method));
    }
    assert.match(LEGACY, /^выберите способ оплаты/);
    assert.deepEqual(run(['invoice_number'], { payment_method: 'invoice' }).map((item) => item.key), ['invoice_number']);
    assert.deepEqual(run(['invoice_file'], {}, true).map((item) => item.key), ['invoice_file'], 'у регулярного платежа поля счёта всегда на экране');
    // Регулярный платёж первой версии: компанию задаёт справочник — пункт у выбора платежа и о справочнике.
    assert.deepEqual(run(['legal_entity_id'], {}, true), [{ key: 'fixed_template_id', text: ENTITY }]);
    assert.match(ENTITY, /справочнике/);
    assert.deepEqual(run(['legal_entity_id'], {}, false).map((item) => item.key), ['legal_entity_id']);
});

test('форма заявки: до попытки позиции и поставщики не красные, блок счёта раскрывается, стрелки ищут поля в форме', () => {
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    assert.ok(form.includes('issue={reveal ? byKey.items?.[0] || null : null}'));
    assert.ok(form.includes('issues={reveal ? byKey.offers || [] : []}'));
    assert.match(form, /useIssueNav\(formRef, keys\)/);
    assert.match(form, /<div key=\{kind \? 'form' : 'kind'\} ref=\{formRef\}/, 'без ref стрелки и подводка молчат');
    const openInvoice = "if (draft.payment_method === 'invoice' && keys.some((key) => key.startsWith('invoice_'))) setInvoiceOpen(true);";
    assert.ok(between(form, 'const trySubmit = (event) => {', 'const save = async').includes(openInvoice), 'trySubmit раскрывает счёт');
    // Пункты счёта появились уже при красных полях (переключили «Как платим» на счёт) — блок раскрывается сам.
    assert.ok(form.includes("if (reveal && !invoiceOpen && draft.payment_method === 'invoice' && keys.some((key) => key.startsWith('invoice_'))) setInvoiceOpen(true);"));
    assert.ok(between(form, 'revealedFor.current = fillKey;', '}, [open, reveal, fillKey, revealIssues]);').includes(openInvoice), '«Доработать» раскрывает счёт');
    assert.ok(between(form, 'revealedFor.current = fillKey;', '}, [open, reveal').includes('nav.first();'));
    assert.ok(form.includes('setReveal(Boolean(revealIssues && data?.request));'), '«Доработать заявку» — поля красные сразу');
});

test('useIssueNav: порядок экрана и текущее поле', () => {
    const ui = source('src/components/payments/paymentsUi.jsx');
    const hook = between(ui, 'export const useIssueNav = (rootRef, keys) => {', 'export const IssueNav');
    assert.ok(hook.includes("const screen = [...root.querySelectorAll('[data-issue-anchor]')]"));
    assert.ok(hook.includes('issueKeys(keysRef.current.map((key) => ({ key })), screen).filter((key) => anchorOf(key))'),
        'стрелки — по экрану, не по правилам, и только по полям, которые на экране есть');
    assert.ok(hook.includes('issueStep(sorted, currentRef.current, pending.how, screen)'));
    assert.ok(hook.includes('revealAnchor(anchorOf(target), { focus: pending.focus });'));
    assert.ok(hook.includes('currentRef.current = target;'), 'без этого каждая стрелка начинала бы с первого поля');
});

/* ── этап: панель причины и имущество ─────────────────────────────────────────── */

test('панель причины: пустая причина не уходит; «Другое» без текста краснеет', () => {
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    const panel = between(stage, 'const ReasonPanel = ', 'const PaymentStageForm = ');
    const rules = new Function('info', 'reason', 'text', 'mode',
        `${between(panel, 'const issues = [];', 'const keys = issueKeys(issues);')} return issues.map((item) => item.key);`);
    assert.deepEqual(rules(false, null, '', 'return'), ['text']);
    assert.deepEqual(rules(false, null, '  ', 'reject'), ['text']);
    assert.deepEqual(rules(false, null, 'Нет счёта', 'return'), []);
    assert.deepEqual(rules(true, null, '', 'request_info'), ['reason']);
    assert.deepEqual(rules(true, 'other', ' ', 'request_info'), ['text']);
    assert.deepEqual(rules(true, 'other', 'Какой счёт', 'request_info'), []);
    assert.deepEqual(rules(true, 'no_invoice', '', 'request_info'), []);
    // Сама кнопка: с пунктами — красим и ведём к полю, onSubmit не зовётся; без пунктов — уходит.
    const makeSubmit = new Function('busy', 'issues', 'setReveal', 'nav', 'onSubmit', 'reason', 'text', 'fromKeyboard',
        `${between(panel, 'const submit = (event) => {', 'const copy = {')} return submit;`);
    const calls = [];
    const run = (issues) => makeSubmit(false, issues, (value) => calls.push(['reveal', value]),
        { first: () => calls.push(['first']) }, (payload) => calls.push(['submit', payload]), 'other', ' x ',
        (event) => Boolean(event && event.detail === 0))({ detail: 1 });
    run([{ key: 'text', text: 'напишите, что доработать' }]);
    assert.deepEqual(calls, [['reveal', true], ['first']], 'с пустой причиной onSubmit не зовётся');
    calls.length = 0;
    run([]);
    assert.deepEqual(calls, [['submit', { reason: 'other', comment: 'x' }]]);
    assert.match(panel, /<div ref=\{panelRef\}/);
});

test('этап: стрелки ищут поля в форме этапа, карточки имущества получают пункты', () => {
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    assert.match(stage, /<div ref=\{formRef\} data-stage-form=\{kind\}/);
    assert.match(stage, /<PaymentAssetsEditor [^>]*issueOf=\{issueOf\}/);
});
