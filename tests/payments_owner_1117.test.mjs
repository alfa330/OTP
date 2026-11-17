/*
 * «Оплата счетов», правки владельца 17.11.2026 — то, что видно в интерфейсе.
 *
 *   пп. 1, 6, 7 — регулярный платёж: повтор словами (как в напоминаниях Google), день прихода
 *          заявки, «Счёт: нужен / не нужен», проверяющий; без счёта форма счёта не просит;
 *   пп. 2–4 — договор: срок и лимит, «обратите внимание» розовым, тревога — сплошным красным;
 *          просрочка и «скоро срок» в метках срока;
 *   п. 5  — автозаполнение: строка «из счёта заполнено» и предупреждение о чужом поставщике;
 *   п. 8  — проверка поставщика по БИН у договора: обязательна у нового, выписка о другом БИН —
 *          ошибка у поля;
 *   п. 9  — готовые проверки: значок и тон по уровню, ссылка на заявку;
 *   п. 10 — журнал: вкладка администратора, «История» у записей.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
    FIELD_LABELS, HINTS, INVOICE_REQUIRED_OPTIONS, KGD_SERVICE_URL, TAX_REGIME_OPTIONS, TAX_VAT_OPTIONS, arrivalISO,
    contractTermView, describeEvent, dueChip, regularIssues, repeatText, taxText, toneRow, tonePill,
} from '../src/components/payments/paymentsMeta.js';
import { PAYMENTS_SECTION_KEYS } from '../src/components/payments/paymentsLink.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-1117-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

async function bundle(entry, name) {
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
    const outPath = join(OUT_DIR, `${name}-${process.pid}.mjs`);
    writeFileSync(outPath, result.outputFiles[0].text);
    return import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
}

const h = React.createElement;
let uiModule = null;
const ui = async () => { uiModule = uiModule || await bundle('src/components/payments/paymentsUi.jsx', 'ui'); return uiModule; };

/* ── пп. 1, 6, 7 ─────────────────────────────────────────────────────────────── */

test('повтор платежа словами — как напоминание в Google', () => {
    assert.equal(repeatText({ periodicity: 'monthly', next_due_on: '2026-12-10' }), 'Каждый месяц 10-го');
    assert.equal(repeatText({ periodicity: 'quarterly', next_due_on: '2027-01-05' }), 'Раз в квартал, 5-го');
    assert.equal(repeatText({ periodicity: 'yearly', next_due_on: '2027-03-15' }), 'Каждый год 15 марта');
    assert.equal(repeatText({ periodicity: 'custom', interval_days: 14 }), 'Каждые 14 дней');
    assert.equal(repeatText({ periodicity: 'monthly' }), 'Ежемесячно');
});

test('заявка приходит 1-го числа месяца оплаты, у своего интервала — заранее', () => {
    assert.equal(arrivalISO({ periodicity: 'monthly', next_due_on: '2026-12-10' }), '2026-12-01');
    assert.equal(arrivalISO({ periodicity: 'yearly', next_due_on: '2027-03-15' }), '2027-03-01');
    assert.equal(arrivalISO({ periodicity: 'custom', next_due_on: '2026-12-10', lead_days: 7 }), '2026-12-03');
    assert.equal(arrivalISO({ periodicity: 'monthly', next_due_on: '' }), '');
});

test('«Счёт: нужен / не нужен» — сегментами с «i», без счёта форма счёта не просит', () => {
    assert.deepEqual(INVOICE_REQUIRED_OPTIONS.map((item) => item.value), [true, false]);
    assert.ok(HINTS.invoiceRequired.options.length === 2 && HINTS.checker.outro && HINTS.paymentDay.outro);
    const draft = { fixed_template_id: 1, department_id: 1, payment_period: 'декабрь 2026', amount: 450000 };
    const template = { counterparty_id: 1, legal_entity_id: 1, object_type: 'service' };
    const keys = (issues) => issues.map((item) => item.key);
    assert.ok(keys(regularIssues(draft, { template })).includes('invoice_file'));
    assert.deepEqual(keys(regularIssues(draft, { template: { ...template, invoice_required: false } })), []);
    assert.equal(FIELD_LABELS.invoice_required, 'Счёт');
});

test('проверку бухгалтерией история называет словами', () => {
    assert.equal(describeEvent({ kind: 'checked', payload: { kind: 'regular_check' } }), 'Проверено бухгалтерией');
});

test('справочник: «Счёт», «Проверяющий» и повтор в строке', () => {
    const panel = source('src/components/payments/FixedPaymentsPanel.jsx');
    assert.match(panel, /label="Счёт"[^>]*hint=\{HINTS\.invoiceRequired\}/);
    assert.match(panel, /label="Проверяющий" hint=\{HINTS\.checker\}/);
    assert.match(panel, /data-repeat-summary/);
    assert.match(panel, /entity="template"/, 'у платежа — «История изменений»');
});

/* ── пп. 2–4 ─────────────────────────────────────────────────────────────────── */

test('срок договора в списке: розовым — подходит, красным — истёк, недействующий — без цвета', () => {
    const today = '2026-11-17';
    assert.deepEqual(contractTermView({ status: 'active' }, { today }), { level: 'ok', text: 'бессрочный', note: '' });
    assert.deepEqual(contractTermView({ status: 'active', ends_on: '2027-03-01' }, { today }), { level: 'ok', text: 'до 01.03.2027', note: '' });
    assert.deepEqual(contractTermView({ status: 'active', ends_on: '2026-12-10' }, { today }),
        { level: 'attention', text: 'до 10.12.2026', note: 'осталось 23 дня' });
    assert.equal(contractTermView({ status: 'active', ends_on: '2026-12-10' }, { today, warnDays: 10 }).level, 'ok');
    assert.deepEqual(contractTermView({ status: 'active', ends_on: '2026-10-31' }, { today }),
        { level: 'alarm', text: 'до 31.10.2026', note: 'срок истёк' });
    assert.equal(contractTermView({ status: 'active', ends_on: today }, { today }).note, 'истекает сегодня');
    assert.equal(contractTermView({ status: 'terminated', ends_on: '2026-10-31' }, { today }).level, 'ok');
});

test('просрочка — кричащий красный, «скоро срок» — мягкий розовый', async () => {
    assert.ok(toneRow('overdue').includes('red'));
    assert.equal(tonePill('overdue').fill, 'bg-red-600 text-white');
    const { DueChip, StatePill } = await ui();
    const overdue = renderToStaticMarkup(h(DueChip, { request: { status: 'active', due_on: '2026-11-01' } }));
    assert.match(overdue, /bg-red-600 text-white/);
    const soonDate = new Date(Date.now() + 5 * 3600 * 1000 + 86400 * 1000).toISOString().slice(0, 10);
    const soon = renderToStaticMarkup(h(DueChip, { request: { status: 'active', due_on: soonDate }, soonDays: 2 }));
    assert.match(soon, /bg-rose-50 text-rose-600/);
    assert.equal(dueChip({ status: 'active', due_on: soonDate }, { soonDays: 2 }).tone, 'soon');
    // Точка внутри сплошной красной метки — белая, иначе её не видно.
    assert.match(renderToStaticMarkup(h(StatePill, { request: { status: 'active', state: 'overdue' } })), /bg-white/);
});

test('договор у участников: номер, срок и лимит с тоном; в моменте оплаты — рамка всего блока', async () => {
    const { ContractTerms } = await ui();
    const view = {
        number: 'К-01', level: 'alarm', amount: 3000000, periodicity: 'monthly',
        term: { level: 'ok', text: 'Действует до 31.12.2026' },
        limit: { level: 'alarm', text: 'Эта оплата выходит за лимит 7 000 ₸ на 2 000 ₸', limit: 7000, paid: 6000, in_work: 0, amount: 3000, this_paid: false },
    };
    const calm = renderToStaticMarkup(h(ContractTerms, { view }));
    assert.match(calm, /data-contract-terms="alarm"/);
    assert.match(calm, /№К-01/);
    assert.match(calm, /data-level="alarm"[^>]*class="[^"]*bg-red-600/);
    assert.doesNotMatch(calm, /ring-red-500/, 'в карточке — без рамки');
    const strong = renderToStaticMarkup(h(ContractTerms, { view, strong: true }));
    assert.match(strong, /bg-red-50 ring-2 ring-red-500/);
    const pink = renderToStaticMarkup(h(ContractTerms, { view: { ...view, level: 'attention', limit: null, term: { level: 'attention', text: 'Действует до 10.12.2026 — осталось 23 дня' } }, strong: true }));
    assert.match(pink, /bg-rose-50\/70 ring-1 ring-rose-200/);
    assert.match(pink, /data-level="attention"[^>]*class="[^"]*bg-rose-50 text-rose-700/);
    assert.equal(renderToStaticMarkup(h(ContractTerms, { view: null })), '');
});

/* ── п. 9 ────────────────────────────────────────────────────────────────────── */

test('готовые проверки: значок и тон по уровню, ссылка на заявку', async () => {
    const { ChecksPanel } = await ui();
    const html = renderToStaticMarkup(h(ChecksPanel, {
        onOpenRequest: () => {},
        checks: [
            { code: 'contract', title: 'Договор', level: 'ok', text: '№1 · Действует до 31.12.2026' },
            { code: 'limit', title: 'Лимит договора', level: 'alarm', text: 'выходит за лимит' },
            { code: 'tax', title: 'Поставщик по БИН', level: 'attention', text: 'не проверен по БИН' },
            { code: 'price', title: 'Прошлые цены', level: 'attention', text: '+25%', request_id: 29 },
            { code: 'duplicate', title: 'Возможный дубль', level: 'none', text: '—' },
        ],
    }));
    assert.match(html, /data-auto-checks/);
    assert.match(html, /data-check="limit" data-level="alarm"/);
    assert.match(html, /data-open-request="29"/);
    assert.match(html, /открыть №29/);
    assert.equal(renderToStaticMarkup(h(ChecksPanel, { checks: [] })), '');
    const form = source('src/components/payments/PaymentStageForm.jsx');
    assert.match(form, /kind === 'manager_approval' \|\| kind === 'approval'\) \{[\s\S]{0,400}<ChecksPanel checks=\{data\.checks/);
    assert.match(form, /if \(kind === 'regular_check'\)/);
    assert.match(form, /<ContractTerms view=\{data\.contract_view\} strong \/>/);
});

/* ── п. 5 и п. 8 ─────────────────────────────────────────────────────────────── */

test('проверка по БИН у договора: обязательна у нового, выписка о другом БИН — ошибка', async () => {
    assert.deepEqual(TAX_VAT_OPTIONS.map((item) => item.label), ['С НДС', 'Без НДС']);
    assert.deepEqual(TAX_REGIME_OPTIONS.map((item) => item.label), ['ОУР', 'СНР']);
    assert.match(KGD_SERVICE_URL, /^https:\/\/portal\.kgd\.gov\.kz\//);
    assert.equal(taxText(false, 'snr', 'на основе упрощённой декларации'), 'Без НДС · СНР (на основе упрощённой декларации)');
    assert.equal(taxText(true, 'our', 'лишнее'), 'С НДС · ОУР');
    assert.equal(taxText(null, null), '');
    const { contractTaxIssues } = await bundle('src/components/payments/PaymentsDictionaries.jsx', 'dictionaries');
    assert.deepEqual(Object.keys(contractTaxIssues({})), ['tax_vat', 'tax_regime']);
    assert.deepEqual(contractTaxIssues({ id: 6 }), {}, 'у заведённого раньше договора проверка не требуется');
    assert.deepEqual(Object.keys(contractTaxIssues({ id: 6, tax_touched: true, tax_vat: false })), ['tax_regime']);
    assert.deepEqual(contractTaxIssues({ tax_vat: false, tax_regime: 'snr' }), {});
    assert.deepEqual(contractTaxIssues({ tax_vat: false, tax_regime: 'snr' }, { status: 'mismatch', text: 'о другом БИН' }),
        { tax_file: 'о другом БИН' });
});

test('счёт заполняет только пустые поля и предупреждает о чужом поставщике', () => {
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    assert.match(form, /api\/payments\/extract/);
    assert.match(form, /form\.append\('kind', 'invoice'\)/);
    assert.match(form, /!String\(prev\.invoice_number \|\| ''\)\.trim\(\)/, 'введённый номер не перетирается');
    assert.match(form, /Счёт выставил/);
    assert.match(form, /onPick=\{pickInvoice\}/);
    assert.match(form, /template\?\.invoice_required !== false/);
});

/* ── п. 10 ───────────────────────────────────────────────────────────────────── */

test('журнал — вкладка администратора и «История» у записей', async () => {
    assert.ok(PAYMENTS_SECTION_KEYS.includes('journal'));
    const view = source('src/components/payments/PaymentsView.jsx');
    assert.match(view, /if \(isAdmin\) list\.push\(\{ value: 'journal', label: 'Журнал' \}\)/);
    const journal = await bundle('src/components/payments/PaymentsJournal.jsx', 'journal');
    assert.equal(typeof journal.default, 'function');
    assert.equal(typeof journal.RecordHistory, 'function');
    assert.equal(renderToStaticMarkup(h(journal.RecordHistory, { apiBaseUrl: '', headers: () => ({}), entity: 'contract', entityId: null })), '');
    assert.match(renderToStaticMarkup(h(journal.RecordHistory, { apiBaseUrl: '', headers: () => ({}), entity: 'contract', entityId: 6 })),
        /История изменений/);
    const dictionaries = source('src/components/payments/PaymentsDictionaries.jsx');
    assert.match(dictionaries, /<RecordHistory apiBaseUrl=\{apiBaseUrl\} headers=\{headers\} entity=\{HISTORY_ENTITY\[target\]\}/);
});
