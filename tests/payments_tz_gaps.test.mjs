/*
 * «Оплата счетов»: доделанные пункты сверки ТЗ 09.11.2026 — что видит человек.
 *
 * Слово владельца: «кроме первого, до 10 все нужно реализовать».
 *   - п. 18 — архив «Удалённые»: состояние, плитка реестра, строка в карточке, событие истории;
 *   - п. 17 — срок чека и закрывающих документов словами;
 *   - п. 8  — НДС поставщика в трёх состояниях, без него «Оплачено» краснеет у поля;
 *   - п. 10.2 — стоимость 0 у карточки имущества — пропуск;
 *   - п. 9  — полный номер карты финотделу стоит сразу, с «Скопировать»;
 *   - п. 7  — короткие имена людей на карточке доски.
 * Как это выглядит на экране, снимает проход стенда.
 *
 * Запуск: node --test tests/payments_tz_gaps.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
    STATE_FILTERS, STATE_META, closedGroupStart, deletedLine, describeEvent, dueChip, isClosed, registryWhere, requestState,
    routeSummary, shortPersonName, stageDeadlineLabel, vatLabel,
} from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-tz-gaps-test/', import.meta.url)
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
    const outPath = join(OUT_DIR, `${name}-${process.pid}.mjs`);
    writeFileSync(outPath, result.outputFiles[0].text);
    const loaded = await import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
    bundles.set(name, loaded);
    return loaded;
}

const h = React.createElement;
const keysOf = (issues) => issues.map((item) => item.key);

test('п. 18: заявка из архива — «Удалена», закрыта, плитка «Удалённые» только с её счётчиком', () => {
    const archived = { status: 'active', state: 'deleted', deleted: true, deleted_at: '2026-11-09T10:15:00', deleted_by_name: 'Админов Аман' };
    assert.equal(requestState(archived), 'deleted');
    assert.equal(STATE_META.deleted.label, 'Удалена');
    assert.ok(isClosed(archived), 'срок и «просрочена» у неё не рисуются');
    assert.ok(!isClosed({ status: 'active' }));
    // Слово «Удалена» стоит меткой рядом — в строке «когда и кем» оно не повторяется.
    assert.equal(deletedLine(archived), '09.11.2026 10:15 · Админов Аман');
    assert.equal(deletedLine({ status: 'active' }), '');
    // Ни флажка срока (телефон), ни «текущего этапа», ни подписи «Завершённые» внутри архива.
    assert.equal(dueChip({ ...archived, due_on: '2026-10-01' }, { today: '2026-11-09' }), null);
    assert.equal(routeSummary(archived, [{ kind: 'approval', state: 'open', title: 'Утверждение', role_label: 'Утвердитель' }]),
        'Удалена на этапе «Утверждение»');
    assert.equal(routeSummary(archived, []), 'Удалена');
    assert.equal(registryWhere(archived), 'Удалена');
    assert.equal(closedGroupStart([{ status: 'active', deleted: true }, { status: 'done', deleted: true }], 1), false);
    assert.equal(closedGroupStart([{ status: 'active' }, { status: 'done' }], 1), true, 'у живых подпись как была');
    const tile = STATE_FILTERS.find((item) => item.key === 'deleted');
    assert.deepEqual(tile, { key: 'deleted', label: 'Удалённые', counter: 'deleted', tone: 'rejected' });
    // Плитка рисуется, только когда её счётчик пришёл и не ноль (его считают только администратору).
    const view = source('src/components/payments/PaymentsView.jsx');
    assert.ok(view.includes(".filter((item) => ['all', 'mine', 'open'].includes(item.key) || counters[item.counter] || state === item.key)"));
    assert.equal(describeEvent({ kind: 'deleted', payload: { status: 'active' } }), 'Заявка удалена — в архиве «Удалённые»');
});

test('п. 18: окно удаления говорит, что останется, и просит причину', () => {
    const card = source('src/components/payments/PaymentRequestCard.jsx');
    assert.ok(card.includes('уйдёт в «Удалённые»: история, файлы и имущество останутся'));
    assert.ok(card.includes("axios.delete(base, { data: { comment }, headers: headers() })"));
    assert.doesNotMatch(card, /Удалить навсегда/);
    assert.ok(card.includes("const active = request?.status === 'active' && !request?.deleted;"));
    // «Ход заявки»: этап, на котором её удалили, — не текущий.
    assert.ok(card.includes("const stopped = Boolean(request?.deleted) && ['open', 'waiting'].includes(item.state);"));
    assert.ok(card.includes("if (stopped) right = 'удалена на этом этапе';"));
    // Реестр: строка архива — без этапа, исполнителя и срока, с тем, когда и кем удалена.
    const view = source('src/components/payments/PaymentsView.jsx');
    assert.ok(view.includes("const active = request.status === 'active' && !request.deleted;"));
    assert.ok(view.includes('{deletedLine(request)}</div>'));
});

test('п. 17: срок этапа словами', () => {
    assert.equal(stageDeadlineLabel({ kind: 'receipt_confirm', due_on: '2026-11-12', days_left: 3 }), 'Чек — до 12.11.2026');
    assert.equal(stageDeadlineLabel({ kind: 'receipt_confirm', due_on: '2026-11-10', days_left: 1 }), 'Чек — срок завтра');
    assert.equal(stageDeadlineLabel({ kind: 'closing_docs', due_on: '2026-11-09', days_left: 0 }), 'Документы — срок сегодня');
    assert.equal(stageDeadlineLabel({ kind: 'closing_docs', due_on: '2026-11-04', days_left: -5 }),
        'Документы — просрочено на 5 дней, срок был 04.11.2026');
    assert.equal(stageDeadlineLabel(null), '');
    assert.equal(stageDeadlineLabel({ kind: 'closing_docs' }), '');
    const settings = source('src/components/payments/PaymentsDictionaries.jsx');
    assert.ok(settings.includes("numberField('receipt_days'"));
    assert.ok(settings.includes("numberField('closing_docs_days'"));
});

test('п. 8: НДС поставщика — три состояния, «не указан» не равно «без НДС»', () => {
    assert.equal(vatLabel(true), 'с НДС');
    assert.equal(vatLabel(false), 'без НДС');
    assert.equal(vatLabel(null), 'НДС не указан');
    assert.equal(vatLabel(undefined), 'НДС не указан');
    assert.equal(describeEvent({ kind: 'supplier_vat', payload: { counterparty_name: 'Sulpak', vat_payer: true } }),
        'НДС поставщика «Sulpak»: с НДС');
    const dictionaries = source('src/components/payments/PaymentsDictionaries.jsx');
    assert.ok(dictionaries.includes('vat_payer: row.vat_payer ?? null'), 'карточка поставщика не превращает «не указан» в «без НДС»');
    assert.doesNotMatch(dictionaries, /row\.vat_payer \? 'с НДС' : 'без НДС', emailsShort/);
});

test('п. 8: «Оплачено» без НДС поставщика краснеет у поля НДС, выбранный — не мешает', async () => {
    const { stageIssues } = await bundle('src/components/payments/PaymentStageForm.jsx', 'stage');
    const ready = { paid: { paid_on: '2026-11-09', paid_amount: 100, supplier_vat: null }, kinds: new Set(['payment_order']) };
    assert.deepEqual(keysOf(stageIssues('invoice_payment', { ...ready, request: { counterparty_id: 7, counterparty_vat: null } })), ['vat']);
    // Выбор в окне (уходит вместе с «Оплачено») снимает пункт; «Без НДС» — тоже выбор, не «ничего».
    for (const choice of [true, false]) {
        assert.deepEqual(stageIssues('invoice_payment', { ...ready, paid: { ...ready.paid, supplier_vat: choice },
            request: { counterparty_id: 7, counterparty_vat: null } }), []);
    }
    assert.deepEqual(stageIssues('invoice_payment', { ...ready, request: { counterparty_id: 7, counterparty_vat: false } }), []);
    assert.deepEqual(stageIssues('invoice_payment', { ...ready, request: { counterparty_id: null, counterparty_vat: null } }), []);
    assert.deepEqual(keysOf(stageIssues('invoice_payment', { paid: { paid_on: '', paid_amount: 0 }, kinds: new Set(), request: { counterparty_id: 7 } })),
        ['vat', 'paid_on', 'paid_amount', 'files'], 'НДС — первым: он выше полей оплаты');
    const form = source('src/components/payments/PaymentStageForm.jsx');
    assert.ok(form.includes('const lastPayments = data.supplier_history'), '«Прошлые оплаты» — выборкой по поставщику');
    assert.ok(form.includes('<Choice value={paid.supplier_vat} options={VAT_OPTIONS} onChange={(value) => setPaid((prev) => ({ ...prev, supplier_vat: value }))}'),
        'выбор подсвечен и меняется до «Оплачено»');
    assert.doesNotMatch(form, /onSetVat|supplier-vat/, 'мгновенной записи по касанию нет');
});

test('п. 10.2: стоимость 0 у карточки имущества — пропуск, как на сервере', async () => {
    const { assetIssues, assetGaps } = await bundle('src/components/payments/PaymentAssetsEditor.jsx', 'assets');
    const asset = {
        key: 'a1', name: 'Ноутбук', category_id: 1, serial_number: 'SN', inventory_number: 'ИНВ-1', received_on: '2026-11-09',
        cost: 350000, legal_entity_id: 1, city: 'Алматы', department_id: 5, responsible_user_id: 7, location: 'Офис', status: 'in_use',
    };
    assert.deepEqual(assetIssues([asset]), []);
    assert.deepEqual(assetIssues([{ ...asset, cost: 0 }]), [{ key: 'asset:a1:cost', text: 'укажите стоимость' }]);
    assert.deepEqual(assetGaps({ ...asset, cost: '' }), ['стоимость']);
    assert.deepEqual(assetGaps({ ...asset, cost: '0,01' }), [], 'тиын — уже стоимость');
});

test('п. 9: полный номер финотделу — сразу и с «Скопировать», без кнопки «Показать»', async () => {
    const { CardNumber } = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const full = renderToStaticMarkup(h(CardNumber, { mask: '•••• 1111', number: '4111 1111 1111 1111', canReveal: true, compact: true }));
    assert.match(full, /data-card-number="full"/);
    assert.match(full, />4111 1111 1111 1111<\/span>/);
    assert.doesNotMatch(full, /Показать номер|Скрыть номер/);
    assert.match(full, /Скопировать номер/);
    const masked = renderToStaticMarkup(h(CardNumber, { mask: '•••• 1111', canReveal: true }));
    assert.match(masked, /data-card-number="mask"/);
    assert.match(masked, /Показать номер/);
    assert.equal(renderToStaticMarkup(h(CardNumber, { mask: '' })), '');
    const board = source('src/components/payments/PaymentsBoard.jsx');
    assert.ok(board.includes("number={card.card_number || ''}"));
});

test('п. 7: имя на карточке доски — «Фамилия И.», подразделение — целиком', () => {
    assert.equal(shortPersonName('Тестов Руслан'), 'Тестов Р.');
    assert.equal(shortPersonName('  Omarova   aru  '), 'Omarova A.');
    assert.equal(shortPersonName('Бухгалтерия', true), 'Бухгалтерия');
    assert.equal(shortPersonName('Финансовый отдел', true), 'Финансовый отдел');
    assert.equal(shortPersonName('Мадина'), 'Мадина');
    assert.equal(shortPersonName(''), '');
});

test('п. 12: смена компании-владельца — своей строкой истории имущества', () => {
    const assets = source('src/components/payments/PaymentsAssets.jsx');
    assert.ok(assets.includes("owner: 'Смена компании-владельца'"));
    // Строка «было → стало» — без повтора подписи: заголовок уже назвал, что сменилось.
    assert.ok(assets.includes("if (move.kind === 'owner') return [`${move.from_legal_entity_name || '—'} → ${move.to_legal_entity_name || '—'}`];"));
    // Первая компания-владелец видна в строке «Поставлено на учёт».
    assert.ok(assets.includes("[move.to_city, move.to_location].filter(Boolean).join(', '), move.to_legal_entity_name || ''].filter(Boolean);"));
    assert.ok(assets.includes("asset.request_deleted && !canOpenDeleted"), 'номер заявки из архива — без ссылки');
    assert.ok(assets.includes('Смена компании-владельца здесь тоже остаётся в истории.'));
});
