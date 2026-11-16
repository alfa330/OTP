/*
 * «Оплата счетов», правки владельца 16.11.2026 — то, что видно в интерфейсе.
 *
 *   п. 1  — «Прошлые закупы» того же товара или услуги: суть для свёрнутой строки окна заявки;
 *   п. 2  — прошлые оплаты, история оплат в форме и дубль счёта — номером-ссылкой;
 *   пп. 3, 4, 10 — платёжное поручение, доверенность, подтверждение перевода «только если
 *           требуется»: выбор сегментами с «i» и обязательность только отмеченного;
 *   пп. 5, 6 — срок закрывающих документов и задача инициатора со сроком;
 *   п. 7  — согласование с бухгалтерией (выбор юр. лица) подставляет компанию инициатора;
 *   пп. 8, 11, 12 — проект и отдел внутри него;
 *   п. 9  — блок Helpdesk IT только у того, кому сервер прислал имущество сотрудника;
 *   п. 13 — «Оплачено» плиткой и меткой.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
    CLOSING_DOC_KINDS, HINTS, PAYMENT_ORDER_OPTIONS, PAYMENT_PROOFS, POWER_OF_ATTORNEY_OPTIONS, STATE_FILTERS, TRANSFER_PROOF_OPTIONS,
    describeEvent, docsDueChip, expenseSubline, projectLine, proofsAsked, similarSummary,
} from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-1116-test/', import.meta.url)
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

/* ── пп. 3, 4, 10: документы оплаты «только если требуется» ──────────────────── */

test('выбор документов оплаты — сегменты «Не нужно / Нужно», подсказка называет ровно их', () => {
    for (const [hint, options] of [['paymentOrder', PAYMENT_ORDER_OPTIONS], ['powerOfAttorney', POWER_OF_ATTORNEY_OPTIONS],
        ['transferProof', TRANSFER_PROOF_OPTIONS]]) {
        assert.deepEqual(options.map((option) => option.value), [false, true], hint);
        assert.deepEqual(HINTS[hint].options.map(([name]) => name), options.map((option) => option.label), hint);
        assert.ok(HINTS[hint].intro.length > 20, hint);
    }
    assert.deepEqual(POWER_OF_ATTORNEY_OPTIONS.map((option) => option.label), ['Не нужна', 'Нужна']);
});

test('просят только отмеченное и только у своего способа оплаты', () => {
    const all = { needs_payment_order: true, needs_power_of_attorney: true, needs_transfer_proof: true };
    assert.deepEqual(proofsAsked({ ...all, payment_method: 'invoice' }).map((item) => item.kind), ['payment_order', 'power_of_attorney']);
    assert.deepEqual(proofsAsked({ ...all, payment_method: 'card' }).map((item) => item.kind), ['transfer_proof']);
    assert.deepEqual(proofsAsked({ payment_method: 'invoice' }), []);
    assert.deepEqual(proofsAsked(null), []);
    // Флаги — те же, что у сервера (workflow.PAYMENT_PROOFS).
    const workflowPy = source('payments/workflow.py');
    for (const item of PAYMENT_PROOFS) assert.match(workflowPy, new RegExp(`\\('${item.flag}', '${item.kind}', METHOD_${item.method.toUpperCase()},`));
});

test('форма заявки отмечает документы оплаты; IT-закуп их не шлёт — их ставит Helpdesk IT', () => {
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    // Новый закуп: счёт — поручение и доверенность, карта — подтверждение перевода; регулярный — как счёт.
    assert.equal(form.split('{invoiceProofFields}').length - 1, 2, 'у счёта нового закупа и у регулярного платежа');
    assert.ok(form.includes("{draft.payment_method === 'card' && cardProofField}"));
    const helpdeskPayload = form.slice(form.indexOf('if (helpdesk) {', form.indexOf('const payloadOf')), form.indexOf('const sent = offers.filter'));
    assert.doesNotMatch(helpdeskPayload, /needs_payment_order|needs_power_of_attorney|needs_transfer_proof/);
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    const helpdesk = stage.slice(stage.indexOf('const passOffers = () => {'), stage.indexOf('const issueStock = async () => {'));
    assert.match(helpdesk, /needs_payment_order: Boolean\(draft\.needs_payment_order\)/);
    assert.match(helpdesk, /needs_transfer_proof: Boolean\(draft\.needs_transfer_proof\)/);
    // Сервер разбирает флаги Helpdesk тем же разбором, что и форму.
    assert.match(source('payments/routes.py'), /'needs_payment_order', 'needs_power_of_attorney', 'needs_transfer_proof'\)/);
});

/* ── пп. 5, 6: срок закрывающих документов и задача инициатора ────────────────── */

test('срок документов — флажком: тон по сроку, подсказка — про документы, а не про оплату', () => {
    const today = '2026-11-16';
    assert.deepEqual(docsDueChip('2026-11-20', { today, soonDays: 2 }), {
        tone: 'normal', icon: 'flag', label: '20.11', title: 'Документы — до 20.11.2026, через 4 дня' });
    assert.equal(docsDueChip('2026-11-17', { today, soonDays: 2 }).tone, 'soon');
    assert.equal(docsDueChip('2026-11-16', { today }).title, 'Документы — срок сегодня, 16.11.2026');
    assert.equal(docsDueChip('2026-11-10', { today }).tone, 'overdue');
    assert.equal(docsDueChip(null), null);
    assert.equal(describeEvent({ kind: 'docs_deferred', payload: {} }), 'Заявка закрыта раньше закрывающих документов');
});

test('задача инициатора: срок, только закрывающие виды файлов и кнопка «Отправить в бухгалтерию»', async () => {
    const { DocsUploadTask } = await bundle('src/components/payments/PaymentStageForm.jsx', 'stage');
    const data = {
        request: { id: 7, docs_tail: true, closing_docs_status: 'none' },
        stage_deadline: { kind: 'closing_docs', due_on: '2026-11-30', days_left: 14 },
    };
    const html = renderToStaticMarkup(h(DocsUploadTask, { data, busy: false, onAttach: async () => true }));
    assert.match(html, /Заявка закрыта, а закрывающие документы бухгалтерия ещё ждёт/);
    assert.match(html, /до 30\.11\.2026/);
    assert.equal((html.match(/30\.11\.2026/g) || []).length, 1, 'срок — один раз, словами; метка с той же датой — лишняя');
    assert.match(html, /Отправить в бухгалтерию/);
    assert.match(html, /data-stage-form="docs_upload"/);
    // Срок прошёл — красная метка «просрочено», а в первой строке даты нет (она в метке).
    const late = renderToStaticMarkup(h(DocsUploadTask, {
        data: { ...data, stage_deadline: { kind: 'closing_docs', due_on: '2026-11-10', days_left: -6 } },
        busy: false, onAttach: async () => true,
    }));
    assert.match(late, /Документы — просрочено на 6 дней, срок был 10\.11\.2026/);
    assert.equal((late.match(/10\.11\.2026/g) || []).length, 1);
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    assert.ok(stage.includes("const DOCS_UPLOAD_KINDS = fileKindOptions('closing_docs').filter((option) => option.value !== 'other');"),
        '«Другое» скан не даст: статус «скан получен» ставят только накладная, акт и чек');
});

test('бухгалтерия: срок документов сохраняется выбором даты, «Закрыть заявку, документы — позже» — с подтверждением', () => {
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    const closing = stage.slice(stage.indexOf("if (kind === 'closing_docs') {"), stage.indexOf('    return null;\n};'));
    assert.match(closing, /onAct\(\{ action: 'docs_due', fields: \{ docs_due_on: value \}, files: \[\] \}\)/);
    // Кнопки — только те, что сервер разрешил: у закрытой раньше документов заявки «закрыть ещё раз» нет.
    assert.ok(closing.includes("{allowed.includes('docs_defer') && ("));
    assert.ok(closing.includes("{allowed.includes('docs_due') && ("));
    assert.ok(closing.includes("{mode === 'docs_defer' && ("));
    assert.ok(closing.includes("onClick={() => act('docs_defer', { docs_due_on: docsDue }, false)}"));
    const routes = source('payments/routes.py');
    assert.ok(routes.includes("actions = [action for action in actions if action in workflow.TAIL_ACTIONS]"));
});

/* ── п. 7: согласование с бухгалтерией ─────────────────────────────────────── */

test('этап бухгалтерии подставляет компанию инициатора и называет её', () => {
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    assert.ok(stage.includes("? request.legal_entity_id : null));"));
    assert.ok(stage.includes("{suggested ? `Инициатор указал: ${suggested}` : 'Инициатор компанию не указал'}"));
    assert.ok(stage.includes('<Check size={14} />} Согласовать'));
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    assert.ok(form.includes('<span>Бухгалтерия <span className="text-slate-500">· выберет юр. лицо</span></span>'));
});

/* ── пп. 8, 11, 12: проект и отдел внутри проекта ──────────────────────────── */

test('проект с отделом — одной строкой; отдел — только у выбранного проекта', () => {
    assert.equal(projectLine({ project_name: 'Tez', project_department_name: 'Колл-центр' }), 'Tez → Колл-центр');
    assert.equal(projectLine({ project_name: 'Tez' }), 'Tez');
    assert.equal(projectLine(null), '');
    assert.equal(expenseSubline({ category_name: 'IT', project_name: 'Tez', project_department_name: 'ОП', department_name: 'СЗоВ' }),
        'IT · Tez → ОП · СЗоВ');
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    assert.ok(form.includes('.filter((item) => draft.project_id && item.project_id === draft.project_id)'));
    assert.ok(form.includes('{projectDepartmentOptions.length > 0 && ('), 'у проекта без отделов поля нет');
    const filters = source('src/components/payments/PaymentsFilters.jsx');
    assert.ok(filters.includes('.filter((item) => item.project_id === filters.project_id)'));
    assert.ok(filters.includes('set({ project_id: value || null, project_department_id: null })'), 'сменили проект — отдел сброшен');
});

test('справочник «Проекты и отделы»: дерево, «Добавить отдел» в проекте, фильтр по проекту', () => {
    const dictionaries = source('src/components/payments/PaymentsDictionaries.jsx');
    assert.ok(dictionaries.includes("label: 'Проекты и отделы'"));
    assert.ok(dictionaries.includes("{name === 'projects' ? 'Добавить проект' : 'Добавить'}"));
    assert.ok(dictionaries.includes('<Plus size={14} /> Добавить отдел'));
    assert.ok(dictionaries.includes("const target = editKind === 'department' ? 'project_departments' : name;"));
    assert.ok(dictionaries.includes("options={[{ value: '', label: 'Все проекты' }"));
    assert.ok(dictionaries.includes('placeholder="Поиск по проектам и отделам"'));
});

/* ── пп. 1, 2: прошлые закупы и ссылки на прошлые заявки ─────────────────────── */

test('«Прошлые закупы»: последний закуп с ценой за единицу и сколько ещё', () => {
    const rows = [
        { request_id: 12, paid_on: '2026-10-01', unit_price: '15000.00', paid_amount: '45000.00' },
        { request_id: 31, paid_on: '2026-11-05', unit_price: '16500.00', paid_amount: '49500.00' },
        { request_id: 7, paid_on: '2026-09-20', unit_price: null, paid_amount: '30000.00' },
    ];
    assert.match(similarSummary(rows), /^№31 · 05\.11 · 16\s500\s₸\/ед\. · ещё 2$/);
    assert.match(similarSummary([rows[2]]), /^№7 · 20\.09 · 30\s000\s₸$/);
    assert.equal(similarSummary([]), '');
    const card = source('src/components/payments/PaymentRequestCard.jsx');
    assert.ok(card.includes('{(data.similar || []).length > 0 && ('), 'нет прошлых закупов — строки нет');
    assert.ok(card.includes('<PastPurchase key={row.request_id} row={row} onOpen={onOpenRequest} />'));
});

test('прошлые оплаты, история оплат и дубль счёта — номером-ссылкой; назад — шевроном', () => {
    const stage = source('src/components/payments/PaymentStageForm.jsx');
    assert.equal((stage.match(/data-open-request=\{/g) || []).length, 2, 'прошлые оплаты и дубль в окне оплаты');
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    assert.ok(form.includes('data-open-request={row.request_id} onClick={() => onOpenRequest(row.request_id)}'));
    const view = source('src/components/payments/PaymentsView.jsx');
    assert.ok(view.includes('onOpenRequest={openFromCard}'));
    // Шеврон окна стопки ведёт к окну под ним (окна стопкой — разбор 16.11.2026).
    assert.ok(view.includes('backTo={depth ? stack[depth - 1] : null}'));
    assert.ok(view.includes('useEffect(() => { if (!openedId) setCardTrail([]); }, [openedId]);'));
});

/* ── п. 9 и п. 13 ──────────────────────────────────────────────────────────── */

test('блок Helpdesk IT рисуется только из имущества, которое сервер прислал', () => {
    const card = source('src/components/payments/PaymentRequestCard.jsx');
    assert.match(card, /\{\(data\.initiator_assets \|\| \[\]\)\.length > 0 && \(\s*<section className="space-y-1\.5" data-card-helpdesk>/);
    assert.doesNotMatch(card, /<Row label="Проверка" wide>/, 'итога проверки в окне больше нет');
});

test('«Оплачено» — плиткой реестра со своим счётчиком', () => {
    const paid = STATE_FILTERS.find((item) => item.key === 'paid');
    assert.deepEqual(paid, { key: 'paid', label: 'Оплачено', counter: 'paid', tone: null });
    assert.ok(STATE_FILTERS.indexOf(paid) < STATE_FILTERS.findIndex((item) => item.key === 'done'));
    assert.match(source('payments/queries.py'), /_PAID_SQL = "r\.status = 'active' AND r\.paid_on IS NOT NULL"/);
});

test('у бухгалтерии в задаче и в колонке закрывающих документов — их срок, а не дата оплаты', () => {
    const desk = source('src/components/payments/PaymentsDesk.jsx');
    assert.match(desk, /\(task\.task === 'docs_needed' \|\| task\.task === 'closing_docs'\) && task\.docs_due_on/);
    const board = source('src/components/payments/PaymentsBoard.jsx');
    assert.match(board, /<DueChip request=\{card\} soonDays=\{soonDays\} docsDueOn=\{card\.column === 'awaiting_docs' \? card\.docs_due_on : null\} \/>/);
});

/* ── Разбор выложенного 16.11.2026 ──────────────────────────────────────────── */

test('форма бухгалтерии по закрытой заявке: срок — полем, в плашке его второй раз нет', async () => {
    const stage = await bundle('src/components/payments/PaymentStageForm.jsx', 'stage-review');
    const PaymentStageForm = stage.default;
    const request = { id: 5, status: 'done', docs_tail: true, closed_at: '2026-11-16T10:00:00', closing_docs_status: 'none',
        payment_method: 'invoice', request_kind: 'purchase', initiator_id: 10 };
    const subtask = { id: 1, kind: 'closing_docs', state: 'open', status: null, title: 'Закрывающие документы' };
    const data = { request, items: [], attachments: [], subtasks: [subtask],
        stage_deadline: { kind: 'closing_docs', due_on: '2026-11-26', days_left: 10 } };
    const props = (actions) => ({ data, subtask, dictionaries: {}, users: [], meta: {}, apiBaseUrl: '', headers: () => ({}),
        busy: false, showToast: () => {}, permissions: { actions }, onAct: () => {}, onTake: () => {}, onEdit: () => {},
        onReply: () => {}, onAttach: () => {} });
    const html = renderToStaticMarkup(h(PaymentStageForm, props(['docs_original', 'docs_close', 'docs_due'])));
    assert.match(html, /Заявка закрыта 16\.11\.2026 раньше закрывающих документов\./);
    assert.equal((html.match(/26\.11\.2026/g) || []).length, 1, 'дата срока — только в поле');
    // Поля срока нет (не бухгалтер) — срок остаётся в плашке.
    const readOnly = renderToStaticMarkup(h(PaymentStageForm, props(['docs_original', 'docs_close'])));
    assert.match(readOnly, /их ждём до 26\.11\.2026/);
    const source_ = source('src/components/payments/PaymentStageForm.jsx');
    assert.match(source_, /if \(!await onAct\(\{ action: 'docs_due', fields: \{ docs_due_on: value \}, files: \[\] \}\)\) setDocsDue\(serverDue\);/,
        'сервер не принял срок — в поле снова сохранённый');
    assert.match(source_, /useEffect\(\(\) => \{ setDocsDue\(serverDue\); \}, \[serverDue\]\);/, 'перечитали заявку — поле со сроком сервера');
});

test('закрывающие документы в интерфейсе — те же виды, что у сервера', () => {
    const server = source('payments/workflow.py').match(/^CLOSING_DOC_KINDS = \(([^)]*)\)/m)[1].match(/'([a-z_]+)'/g)
        .map((item) => item.slice(1, -1));
    assert.deepEqual(CLOSING_DOC_KINDS, server);
    const card = source('src/components/payments/PaymentRequestCard.jsx');
    assert.match(card, /const alive = active \|\| \(tail && CLOSING_DOC_KINDS\.includes\(attachment\.kind\)\);/,
        'свой закрывающий документ закрытой раньше документов заявки снимается, как в идущей');
});

test('окна заявок — стопкой: открытая из окна заявка не размонтирует прежнюю', () => {
    const view = source('src/components/payments/PaymentsView.jsx');
    assert.match(view, /\[cardTrail\[0\] \?\? openedId, \.\.\.cardTrail\.slice\(1\), \.\.\.\(cardTrail\.length \? \[openedId\] : \[\]\)\]\.map\(\(id, depth, stack\) =>/);
    assert.match(view, /<div key=\{depth \? `\$\{depth\}:\$\{id\}` : 'base'\} hidden=\{depth !== stack\.length - 1\}>/,
        'нижние окна спрятаны, а не сняты: набранное в форме этапа дожидается возврата');
    assert.match(view, /onClose=\{depth \? backFromCard : closeCard\}/, 'крестик и жест «назад» верхнего окна — к прежнему');
    assert.match(view, /const below = cardTrail\.indexOf\(next\);/, 'заявка уже в стопке — возврат к ней, без второго окна');
});

test('форма заявки: крестик у «Проекта» убирает и отдел; скрытый отдел заявки виден с пометкой', () => {
    const form = source('src/components/payments/PaymentRequestForm.jsx');
    assert.match(form, /else if \(key === 'project_id'\) pickProject\(null\);/);
    assert.match(form, /list\.push\(\{ value: saved, label: `\$\{request\.project_department_name\} · скрыт` \}\);/);
});

test('справочник: проект у заведённого отдела только для чтения', () => {
    const dict = source('src/components/payments/PaymentsDictionaries.jsx');
    assert.match(dict, /\{isNew \? \(\s*<Field label="Проект" required/);
    assert.match(dict, /<div data-department-project className="[^"]*">\{parent\?\.name \|\| '—'\}<\/div>/);
});
