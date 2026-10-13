/*
 * «Оплата счетов»: окно заявки из сводки, двух страниц и нижней панели.
 *
 * Две просьбы владельца 13.10.2026 об окне заявки.
 * 1. «Кнопку новые / на рассмотрении вообще не в удобном месте — они должны быть внизу, на
 *    месте примерно кнопки готово; такие же переключатели статусов переделать; всё аккуратно
 *    и удобно, в стиле ios/macos». Переключатель статуса задачи стоял вверху, внутри блока
 *    задачи, между пояснением и полем комментария.
 * 2. «Открывается сама заявка, далее кнопка чтобы пролистать и далее действия данного
 *    человека; карточка сверху остаётся, меняется то, что снизу, — для одной модалки слишком
 *    много информации».
 *
 * Теперь: сводка заявки закреплена наверху окна; под ней две страницы — «Заявка» и «Ваша
 * задача», — которые листаются кнопкой в нижней панели; статус задачи переключается там же,
 * в нижней панели, и выбранный сегмент переезжает, как в iOS.
 *
 * Здесь — то, что видно без браузера: какие статусы и у каких этапов, как рисуются
 * переключатель и лента этапов, и что окно действительно собрано из этих частей (по
 * исходнику). Поведение — перелистывание, сохранность набранного в форме, мгновенный выбор
 * статуса и его откат при отказе сервера — снимают проходы стенда.
 *
 * Запуск: node --test tests/payments_card_pages.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import { EDGE_FADE, STAGE_STATUS_KINDS, edgeFade, stageStatusOptions } from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешние react и
// lucide-react не резолвятся. Каталог .cache не отслеживается git.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-card-pages-test/', import.meta.url)
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
    const outPath = join(OUT_DIR, `${name}.mjs`);
    writeFileSync(outPath, result.outputFiles[0].text);
    const loaded = await import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
    bundles.set(name, loaded);
    return loaded;
}

const h = React.createElement;

/* Справочные данные раздела — как их отдаёт сервер (`meta` в /ping). */
const META = {
    boards: [
        { code: 'approval', columns: [{ key: 'new', label: 'Новые' }, { key: 'review', label: 'На рассмотрении' }, { key: 'clarification', label: 'Требуется уточнение' }, { key: 'approved', label: 'Согласовано' }] },
        { code: 'accounting', columns: [{ key: 'new', label: 'Новые счета' }, { key: 'checking', label: 'Проверка' }, { key: 'clarification', label: 'Требуется уточнение' }, { key: 'ready', label: 'Готово к оплате' }, { key: 'paying', label: 'На оплате' }, { key: 'paid', label: 'Оплачено' }] },
        { code: 'finance', columns: [{ key: 'new', label: 'Новые' }, { key: 'in_progress', label: 'В работе' }, { key: 'ready', label: 'Готово к пополнению' }, { key: 'topped_up', label: 'Пополнено' }] },
    ],
    subtasks: [
        { kind: 'initiation', board: null, statuses: ['draft', 'rework', 'clarification'] },
        { kind: 'manager_approval', board: 'approval', statuses: ['new', 'review'] },
        { kind: 'approval', board: 'approval', statuses: ['new', 'review'] },
        { kind: 'invoice_payment', board: 'accounting', statuses: ['new', 'checking', 'ready', 'paying'] },
        { kind: 'card_topup', board: 'finance', statuses: ['new', 'in_progress', 'ready'] },
        { kind: 'receipt_confirm', board: null, statuses: ['new'] },
        { kind: 'receiving', board: null, statuses: ['new'] },
        { kind: 'asset_registration', board: null, statuses: ['new', 'in_progress'] },
        { kind: 'closing_docs', board: 'accounting', statuses: ['awaiting'] },
    ],
};

/* ── какие статусы и у каких этапов ──────────────────────────────────────── */

test('статусы задачи — те же, что колонки доски этапа', () => {
    assert.deepEqual(stageStatusOptions(META, 'approval'), [{ value: 'new', label: 'Новые' }, { value: 'review', label: 'На рассмотрении' }]);
    assert.deepEqual(stageStatusOptions(META, 'manager_approval'), stageStatusOptions(META, 'approval'));
    assert.deepEqual(stageStatusOptions(META, 'invoice_payment').map((option) => option.label), ['Новые счета', 'Проверка', 'Готово к оплате', 'На оплате']);
    assert.deepEqual(stageStatusOptions(META, 'card_topup').map((option) => option.label), ['Новые', 'В работе', 'Готово к пополнению']);
    // У учёта имущества доски нет — подписи свои.
    assert.deepEqual(stageStatusOptions(META, 'asset_registration'), [{ value: 'new', label: 'Новая' }, { value: 'in_progress', label: 'В работе' }]);
});

test('этапам инициатора и этапам с одним статусом переключатель не положен', () => {
    // У «Инициации» три статуса, но меняет их действие (отправить, доработать), а не переключатель.
    assert.deepEqual(stageStatusOptions(META, 'initiation'), []);
    for (const kind of ['receipt_confirm', 'receiving', 'closing_docs']) assert.deepEqual(stageStatusOptions(META, kind), [], kind);
    assert.deepEqual(STAGE_STATUS_KINDS, ['manager_approval', 'approval', 'invoice_payment', 'card_topup', 'asset_registration']);
    // Справочные данные ещё не пришли или этапа в них нет — переключать нечего, но и не падаем.
    for (const meta of [null, undefined, {}, { subtasks: [] }, { boards: [], subtasks: [{ kind: 'approval' }] }]) assert.deepEqual(stageStatusOptions(meta, 'approval'), []);
    assert.deepEqual(stageStatusOptions(META, 'no_such_stage'), []);
    assert.deepEqual(stageStatusOptions(META, undefined), []);
    // У этапа один рабочий статус — переключать не из чего: переключателя из одной кнопки не бывает.
    const single = { boards: META.boards, subtasks: [{ kind: 'approval', board: 'approval', statuses: ['new'] }] };
    assert.deepEqual(stageStatusOptions(single, 'approval'), []);
    // Доска не описывает статус — показываем его как есть, а не пустую кнопку.
    const odd = { boards: [{ code: 'approval', columns: [] }], subtasks: [{ kind: 'approval', board: 'approval', statuses: ['new', 'review'] }] };
    assert.deepEqual(stageStatusOptions(odd, 'approval'), [{ value: 'new', label: 'Новая' }, { value: 'review', label: 'review' }]);
});

/* ── переключатель: выбранный сегмент переезжает ─────────────────────────── */

const OPTIONS = [{ value: 'new', label: 'Новые' }, { value: 'review', label: 'На рассмотрении' }];
const tabs = (html) => [...html.matchAll(/<button[^>]*role="tab"[^>]*aria-selected="(true|false)"[^>]*class="([^"]*)"[^>]*>(.*?)<\/button>/g)]
    .map((m) => ({ selected: m[1] === 'true', cls: m[2], text: m[3].replace(/<[^>]+>/g, '').trim() }));

test('сегментный переключатель без slide рисуется как раньше', async () => {
    const { IosSegmented } = await bundle('src/components/ui/ios.jsx', 'ios');
    const html = renderToStaticMarkup(h(IosSegmented, { value: 'review', options: OPTIONS, onChange: () => {}, ariaLabel: 'Режим' }));
    assert.match(html, /^<div role="tablist" aria-label="Режим" class="inline-flex rounded-\[10px\] p-\[3px\] bg-slate-100 ">/);
    assert.doesNotMatch(html, /data-segment-thumb|relative rounded/);
    const list = tabs(html);
    assert.deepEqual(list.map((tab) => [tab.text, tab.selected]), [['Новые', false], ['На рассмотрении', true]]);
    assert.match(list[1].cls, /bg-white text-slate-900 shadow-\[0_1px_3px_rgba\(15,23,42,0\.10\)\]/);
    assert.doesNotMatch(list[0].cls, /bg-white/);
});

test('slide: контейнер держит плашку выбора, а до замера выбранный сегмент красит себя сам', async () => {
    const { IosSegmented } = await bundle('src/components/ui/ios.jsx', 'ios');
    const html = renderToStaticMarkup(h(IosSegmented, { value: 'new', options: OPTIONS, onChange: () => {}, ariaLabel: 'Статус задачи', slide: true }));
    // Плашка позиционируется от контейнера.
    assert.match(html, /^<div role="tablist" aria-label="Статус задачи" class="inline-flex relative rounded-\[10px\]/);
    // Первого кадра «без выбора» нет: замера ещё не было (на сервере его и не будет) — сегмент закрашен сам.
    assert.doesNotMatch(html, /data-segment-thumb/);
    const list = tabs(html);
    assert.equal(list[0].selected, true);
    assert.match(list[0].cls, /bg-white text-slate-900 shadow-/);
    const ios = source('src/components/ui/ios.jsx');
    // После замера выбранный сегмент отдаёт заливку плашке — иначе их было бы две, и старая не уезжала бы, а гасла.
    assert.ok(ios.includes("? (sliding ? 'text-slate-900' : 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.10)]')"));
    assert.ok(ios.includes('transition-[transform,width] duration-200 ease-[cubic-bezier(0.4,0,0.2,1)] motion-reduce:transition-none'));
    assert.ok(ios.includes("style={{ width: thumb.width, height: thumb.height, transform: `translate(${thumb.left}px, ${thumb.top}px)` }}"));
    // Плашка идёт в разметке раньше кнопок: кнопки (relative) рисуются поверх неё.
    assert.ok(ios.indexOf('data-segment-thumb') < ios.indexOf('role="tab"'));
});

test('переключатель статуса: название для читалки, выбран статус задачи, на узком экране — во всю строку', async () => {
    const { StageStatusSwitch } = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const html = renderToStaticMarkup(h(StageStatusSwitch, { options: stageStatusOptions(META, 'invoice_payment'), value: 'checking', onMove: () => {} }));
    assert.match(html, /data-stage-status/);
    assert.match(html, /role="tablist" aria-label="Статус задачи"/);
    const list = tabs(html);
    assert.deepEqual(list.map((tab) => tab.text), ['Новые счета', 'Проверка', 'Готово к оплате', 'На оплате']);
    assert.deepEqual(list.filter((tab) => tab.selected).map((tab) => tab.text), ['Проверка']);
    // До 640 px — строка целиком и равные сегменты; шире — по ширине подписей. Стилями, по границе
    // раскладки панели, а не по признаку «телефон»: тот включается и на широком экране малой высоты.
    assert.match(html, /class="inline-flex relative [^"]*min-w-max max-sm:flex max-sm:w-full max-sm:\[&amp;&gt;button\]:flex-1"/);
    // Не помещается — листается, без своей полосы.
    assert.match(html, /<div data-stage-status="true" class="max-w-full overflow-x-auto transition-opacity \[scrollbar-width:none\] "/);
    // Пока идёт другое действие над заявкой — приглушён и не нажимается.
    const busy = renderToStaticMarkup(h(StageStatusSwitch, { options: OPTIONS, value: 'new', busy: true, onMove: () => {} }));
    assert.match(busy, /pointer-events-none opacity-60/);
    const ui = source('src/components/payments/paymentsUi.jsx');
    const sw = ui.slice(ui.indexOf('export const StageStatusSwitch = '), ui.indexOf('export const ErrorBox = '));
    // Выбор встаёт сразу; ответ сервера (или отказ) его снимает — в заявке уже новый статус либо прежний.
    assert.ok(sw.includes('const shown = wanted ?? value;'));
    assert.ok(sw.includes('if (busy || wanted !== null || next === value) return;'));
    assert.match(sw, /setWanted\(next\);\s*try \{\s*await onMove\?\.\(next\);\s*\} finally \{[\s\S]*?setWanted\(null\);\s*\}/);
});

test('этапы в сводке: на телефоне — одной листающейся строкой, текущий этап помечен', async () => {
    const { Lifecycle } = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const stages = [
        { code: 'init', label: 'Инициация закупа', state: 'done' },
        { code: 'approval', label: 'Согласование', state: 'current' },
        { code: 'assets', label: 'Учёт имущества', state: 'skipped' },
        { code: 'payment', label: 'Оплата', state: 'pending' },
    ];
    const wide = renderToStaticMarkup(h(Lifecycle, { stages }));
    assert.match(wide, /<ol class="flex items-center gap-1\.5 flex-wrap">/);
    const narrow = renderToStaticMarkup(h(Lifecycle, { stages, oneLine: true }));
    // !flex-nowrap: телефонная оболочка сама переносит ряды с gap-* — здесь перенос запрещён жёстко.
    assert.match(narrow, /<ol class="flex items-center gap-1\.5 !flex-nowrap overflow-x-auto \[scrollbar-width:none\]">/);
    for (const html of [wide, narrow]) {
        assert.equal((html.match(/<li /g) || []).length, 3, 'пропущенный этап не рисуется');
        assert.equal((html.match(/aria-current="step"/g) || []).length, 1);
        assert.match(html, /<li class="flex shrink-0" aria-current="step"><span[^>]*bg-blue-600[^>]*>Согласование<\/span>/);
        // До замера гасить нечего: маска появляется только у края, за которым есть этапы.
        assert.doesNotMatch(html, /mask-image/);
    }
});

test('край ленты гаснет только там, где за ним что-то есть', () => {
    assert.equal(edgeFade(false, false), undefined, 'лента поместилась — маски нет вовсе');
    const left = edgeFade(true, false, 20);
    assert.equal(left.maskImage, 'linear-gradient(to right, transparent 0, #000 20px, #000 calc(100% - 0px), #000 100%)');
    const right = edgeFade(false, true, 20);
    assert.equal(right.maskImage, 'linear-gradient(to right, #000 0, #000 0px, #000 calc(100% - 20px), transparent 100%)');
    const both = edgeFade(true, true, 20);
    assert.equal(both.maskImage, 'linear-gradient(to right, transparent 0, #000 20px, #000 calc(100% - 20px), transparent 100%)');
    // Safari до 15.4 понимает маску только с приставкой.
    for (const style of [left, right, both]) assert.equal(style.WebkitMaskImage, style.maskImage);
    // Без размера — край доски, каким он был: 44 px.
    assert.equal(EDGE_FADE, 44);
    assert.equal(edgeFade(true, true).maskImage, 'linear-gradient(to right, transparent 0, #000 44px, #000 calc(100% - 44px), transparent 100%)');
});

test('лента этапов встаёт на текущий этап и гасит край до первой отрисовки', () => {
    const ui = source('src/components/payments/paymentsUi.jsx');
    const strip = ui.slice(ui.indexOf('export const Lifecycle = '), ui.indexOf('export const AmountInput = '));
    assert.ok(strip.length > 800, 'разметка ленты этапов не найдена');
    // Оба шага — в layout-эффекте: в обычном лента кадр стояла бы в начале и без погашенного края.
    assert.equal((strip.match(/useIsoLayoutEffect\(/g) || []).length, 2);
    assert.doesNotMatch(strip, /\buseEffect\(/);
    assert.ok(strip.includes('style={oneLine ? edgeFade(edges.left, edges.right, LIFECYCLE_EDGE) : undefined}'));
    // Край пересчитывается, когда ленту листают и когда меняется её ширина (поворот телефона).
    assert.ok(strip.includes("list.addEventListener('scroll', read, { passive: true });"));
    assert.ok(strip.includes('new ResizeObserver(read)'));
});

test('заявку вернули — кто и зачем: одна плашка и для страницы заявки, и для формы задачи', async () => {
    const { ReturnNotice } = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const rework = renderToStaticMarkup(h(ReturnNotice, { request: { clarify_reason: 'rework', clarify_by_name: 'Иванова Айгерим', clarify_comment: 'Приложите счёт.' }, subtask: {} }));
    assert.match(rework, /Вернули на доработку — Иванова Айгерим\./);
    assert.match(rework, /Приложите счёт\./);
    const asked = renderToStaticMarkup(h(ReturnNotice, { request: { clarify_reason: 'no_invoice' }, subtask: { clarify_label: 'Счёт не приложен' } }));
    assert.match(asked, /Счёт не приложен\./);
    assert.equal(renderToStaticMarkup(h(ReturnNotice, { request: { clarify_reason: null }, subtask: {} })), '');
    assert.equal(renderToStaticMarkup(h(ReturnNotice, { request: null, subtask: null })), '');
    const form = source('src/components/payments/PaymentStageForm.jsx');
    const card = source('src/components/payments/PaymentRequestCard.jsx');
    assert.ok(form.includes('<ReturnNotice request={request} subtask={subtask} />'));
    assert.ok(card.includes("{hasTask && current.kind === 'initiation' && <ReturnNotice request={request} subtask={current} />}"));
});

/* ── окно: сводка, две страницы, нижняя панель ───────────────────────────── */

test('переключателя статуса в форме этапа больше нет — он в нижней панели окна', () => {
    const form = source('src/components/payments/PaymentStageForm.jsx');
    assert.doesNotMatch(form, /IosSegmented|statusSwitch|onMove|LOCAL_STATUS_LABELS/);
    const card = source('src/components/payments/PaymentRequestCard.jsx');
    const footer = card.slice(card.indexOf('    const footer = ('), card.indexOf('    const view = recordView('));
    assert.ok(footer.length > 800, 'нижняя панель не найдена');
    assert.ok(footer.includes('<StageStatusSwitch key={`${request.id}:${current.kind}`} options={statusOptions} value={current.status} busy={busy} onMove={move} />'));
    // Только тому, чья задача, и только на этапах с рабочими статусами.
    assert.ok(card.includes('() => (hasTask ? stageStatusOptions(meta, current.kind) : []),'));
    assert.ok(card.includes('const hasTask = Boolean(current && permissions.can_act);'));
    // На телефоне — отдельной строкой над кнопками; на компьютере — слева, сразу за меню «…».
    assert.ok(footer.includes('<div className="order-first w-full min-w-0 sm:order-none sm:mr-auto sm:w-auto">'));
    assert.ok(footer.includes("<div className={statusOptions.length ? 'mr-auto sm:mr-0' : 'mr-auto'}>"));
    assert.ok(!card.includes('onMove={move}\n                                            onEdit'), 'форме этапа снова передают смену статуса');
});

test('сводка закреплена над страницами, страниц две, и обе живут в разметке', () => {
    const card = source('src/components/payments/PaymentRequestCard.jsx');
    const summary = card.indexOf('<div data-card-summary className="shrink-0 ');
    const scroller = card.indexOf('ref={pageScrollRef}');
    const requestPage = card.indexOf('<div data-card-page="request" hidden={onTask}');
    const taskPage = card.indexOf('<div data-card-page="task" hidden={!onTask}');
    assert.ok(summary > 0 && scroller > summary && requestPage > scroller && taskPage > requestPage, 'порядок частей окна нарушен');
    // Сводка — вне прокрутки страниц: тело окна — столбик, прокрутка — у страниц.
    assert.ok(card.includes('bodyClassName="thin-scroll flex min-h-0 flex-1 flex-col overflow-hidden [@media(max-height:559px)]:overflow-y-auto"'));
    assert.ok(card.includes("const CARD_SCROLL = 'thin-scroll min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain px-4 sm:px-5 [@media(max-height:559px)]:flex-none [@media(max-height:559px)]:overflow-visible';"));
    // Страница задачи есть только у того, чья задача, но в разметке она сразу и при перелистывании
    // прячется, а не снимается: набранное в форме не теряется.
    assert.match(card, /\{hasTask && \(\s*<div data-card-page="task" hidden=\{!onTask\}/);
    assert.ok(card.indexOf('<PaymentStageForm', taskPage) > taskPage);
    assert.equal(card.split('<PaymentStageForm').length - 1, 1);
    // Чужая задача — страница заявки говорит, у кого заявка; своей задачи над заявкой больше нет.
    assert.ok(card.includes('{current && !hasTask && ('));
    assert.ok(card.includes('<SectionTitle>Сейчас · {current.title}</SectionTitle>'));
    assert.ok(card.includes('<SectionTitle>Ваша задача · {current.title}</SectionTitle>'));
    // Отказ сервера и панели редких действий — над обеими страницами и пустыми не рисуются.
    assert.ok(card.includes('{data && error && <div ref={errorRef} className="scroll-mt-4"><ErrorBox text={error} /></div>}'));
    assert.ok(card.includes("{mode && !mode.startsWith('reassign') && ("));
    const shared = card.indexOf('{data && error && <div ref={errorRef}');
    assert.ok(shared > scroller && shared < requestPage);
    // Короткое предупреждение о дубле стоит в заявке у всех: подробное — в форме оплаты, на другой странице.
    assert.ok(card.includes('{(data.duplicates || []).length > 0 && !request.paid_on && active && ('));
});

test('страницы листаются кнопкой в нижней панели; синяя кнопка в панели одна', () => {
    const card = source('src/components/payments/PaymentRequestCard.jsx');
    const footer = card.slice(card.indexOf('    const footer = ('), card.indexOf('    const view = recordView('));
    // Со страницы заявки — «К задаче», синяя, крайняя справа; «Готово» при этом обычная.
    assert.match(footer, /<button type="button" className=\{hasTask \? iosBtnSecondary : iosBtnPrimary\} onClick=\{onClose\}>Готово<\/button>\s*\{hasTask && !onTask && \(\s*<button type="button" data-card-turn="task" className=\{iosBtnPrimary\} onClick=\{goTask\}>\s*К задаче <ChevronRight/);
    // Со страницы задачи — «К заявке», левее «Готово», обе обычные: синяя там — действие самой формы.
    assert.match(footer, /\{onTask && \(\s*<button type="button" data-card-turn="request" className=\{iosBtnSecondary\} onClick=\{goRequest\}>\s*<ChevronLeft[^>]*\/> К заявке\s*<\/button>\s*\)\}\s*<button type="button" className=\{hasTask/);
    assert.equal(footer.split('iosBtnPrimary').length - 1, 2);
    // «Доработать» — действие над заявкой: на странице задачи его в панели нет.
    assert.ok(footer.includes('{request && permissions.can_edit && !onTask && ('));
    // Шеврон в шапке на странице задачи ведёт к заявке, а не закрывает окно.
    assert.ok(card.includes('onBack={onTask ? goRequest : null}'));
    // Окно открывается на заявке; задачу выполнили или она ушла — возвращается к заявке, к её началу.
    assert.ok(card.includes("const [page, setPage] = useState('request');"));
    assert.match(card, /setFolds\(\{\}\);\s*\/\/[^\n]*\n\s*setPage\('request'\);\s*setPageMotion\(PAGE_APPEAR\);\s*requestScroll\.current = 0;/);
    assert.match(card, /if \(seenTask\.current === taskKind\) return;\s*seenTask\.current = taskKind;\s*if \(page !== 'task'\) return;\s*requestScroll\.current = 0;\s*turn\('request', PAGE_BACK\);/);
    // Страница задачи приезжает справа, заявка возвращается слева; заявка — на то место, где её читали.
    assert.ok(card.includes("const PAGE_FORWARD = 'motion-safe:animate-push-in';"));
    assert.ok(card.includes("const PAGE_BACK = 'motion-safe:animate-pop-in';"));
    assert.ok(card.includes('if (node) node.scrollTop = onTask ? 0 : requestScroll.current;'));
    assert.ok(card.includes('requestScroll.current = pageScroller()?.scrollTop || 0;'));
});
