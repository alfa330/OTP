/*
 * «Оплата счетов»: списки записей — «Заявки», «Имущество», «Справочники».
 *
 * Просьба владельца 13.10.2026: эти вкладки «нужно переделать, чтобы разделение было более
 * заметным и понятным на первый взгляд, так как на данный момент это не так». Записи стояли
 * сплошной таблицей в одной белой карточке (строки разделяла едва видная линия), деление
 * заявок на состояния было мелкой серой полосой, двенадцать справочников — одним списком, а
 * сведения записи справочника — одной серой строкой через точки.
 *
 * Теперь, по образцу списков «Задач»:
 *   - срезы («Все / Ждут меня / В работе…», статусы имущества) — плитками со счётчиками;
 *   - каждая запись — своя строка-карточка с зазором, подписи колонок стоят над списком;
 *   - завершённые заявки отделены подписью; тип заявки — цветной полосой у края строки;
 *   - справочники слева — группами под подписями, со значками; сведения записи — по колонкам,
 *     а где таблица не помещается в своё поле — записи идут карточками.
 *
 * Здесь — то, что видно без браузера. Поведение (отбор плиткой, открытие записи, таблица или
 * карточки по ширине поля, телефон) снимают проходы стенда.
 *
 * Запуск: node --test tests/payments_lists_cards.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
    ASSET_STATUS_META, STATE_FILTERS, TONE_EDGE, TONE_ROW, closedGroupStart, toneEdge, toneRow,
} from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешние react и
// lucide-react не резолвятся. Каталог .cache не отслеживается git.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-lists-cards-test/', import.meta.url)
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
const ui = () => bundle('src/components/payments/paymentsUi.jsx', 'ui');
const dictionaries = () => bundle('src/components/payments/PaymentsDictionaries.jsx', 'dictionaries');

const h = React.createElement;

// ── Плитки ────────────────────────────────────────────────────────────────────

const TILES = [
    { key: 'all', label: 'Все', tone: null, count: 396 },
    { key: 'mine', label: 'Ждут меня', tone: null, count: 0 },
    { key: 'clarification', label: 'Требуют уточнения', tone: 'blocked', count: 2 },
    { key: 'overdue', label: 'Просрочены', tone: 'overdue', count: 0 },
    { key: 'done', label: 'Закрыты', tone: 'done', count: 122 },
    { key: 'cancelled', label: 'Отменены', tone: 'rejected', count: 40 },
    { key: 'unknown', label: 'Счёт не пришёл', tone: null },
];
const tile = (html, key) => (html.match(new RegExp(`<button type="button" data-state-filter="${key}"[\\s\\S]*?</button>`)) || [''])[0];

test('плитка — и число, и отбор: выбранная залита, остальные — карточками', async () => {
    const { StatTiles } = await ui();
    const html = renderToStaticMarkup(h(StatTiles, { ariaLabel: 'Состояние заявок', value: 'all', items: TILES }));
    assert.match(html, /^<div role="group" aria-label="Состояние заявок" data-stat-tiles="wrap"/);
    assert.equal((html.match(/<button /g) || []).length, TILES.length);
    assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);
    // Выбранная: тёмная заливка, число белым.
    const all = tile(html, 'all');
    assert.match(all, /aria-pressed="true"/);
    assert.match(all, /border-slate-900 bg-slate-900/);
    assert.match(all, /text-white">396<\/span>/);
    // Невыбранная — карточка: белая, с рамкой и тенью, как карточки раздела.
    const mine = tile(html, 'mine');
    assert.match(mine, /aria-pressed="false"/);
    assert.match(mine, /border-slate-200 bg-white shadow-\[0_1px_2px_rgba\(15,23,42,0\.06\)\]/);
    assert.match(mine, /Ждут меня<\/span>/);
});

test('цвет у числа — только у состояний со смыслом и только когда записи есть', async () => {
    const { StatTiles } = await ui();
    const html = renderToStaticMarkup(h(StatTiles, { ariaLabel: 'Состояние заявок', value: 'all', items: TILES }));
    assert.match(tile(html, 'clarification'), /text-amber-600">2<\/span>/);
    assert.match(tile(html, 'done'), /text-emerald-600">122<\/span>/);
    assert.match(tile(html, 'cancelled'), /text-slate-400">40<\/span>/);
    // Ноль просроченных красным не красится: красный ноль читался бы как тревога.
    assert.match(tile(html, 'overdue'), /text-slate-900">0<\/span>/);
    assert.match(tile(html, 'mine'), /text-slate-900">0<\/span>/);
    // Точка — легенда цвета строк: есть у состояний с цветом, нет у срезов «Все» и «Ждут меня».
    assert.match(tile(html, 'clarification'), /rounded-full align-middle bg-amber-500/);
    assert.match(tile(html, 'overdue'), /bg-rose-500/);
    assert.doesNotMatch(tile(html, 'all'), /rounded-full align-middle/);
    assert.doesNotMatch(tile(html, 'mine'), /rounded-full align-middle/);
    // Счётчик ещё не пришёл — прочерк, а не «undefined» и не ноль.
    assert.match(tile(html, 'unknown'), />—<\/span>/);
    // Выбранная плитка с цветом: число белое, цвет остаётся точке.
    const picked = renderToStaticMarkup(h(StatTiles, { ariaLabel: 'Состояние заявок', value: 'done', items: TILES }));
    assert.match(tile(picked, 'done'), /text-white">122<\/span>/);
    assert.match(tile(picked, 'all'), /text-slate-900">396<\/span>/);
});

test('плитки переносятся сами и не шире предела; раскладка не из тех, что ломает оболочка телефона', async () => {
    const { StatTiles } = await ui();
    const html = renderToStaticMarkup(h(StatTiles, { ariaLabel: 'Статус имущества', value: '', items: TILES.slice(0, 3) }));
    // Сколько плиток в строке, сетка считает по наименьшей ширине (148 px), а предел в 224 px задан
    // всему ряду: три плитки не растягиваются на экран, восемь — не идут в два ряда раньше времени.
    assert.match(html, /style="grid-template-columns:repeat\(auto-fit, minmax\(148px, 1fr\)\);max-width:688px"/);
    const eight = renderToStaticMarkup(h(StatTiles, { ariaLabel: 'Состояние заявок', value: 'all', items: [...TILES, { key: 'x', label: 'Ещё', count: 1 }] }));
    assert.match(eight, /max-width:1848px/);
    // Оболочка телефона схлопывает сетки с классом grid-cols-[…] и переносит flex-ряды с gap-* — здесь ни того, ни другого.
    const root = html.match(/^<div[^>]*>/)[0];
    assert.doesNotMatch(root, /grid-cols-\[/);
    assert.match(root, /class="grid gap-2 "/);
    assert.doesNotMatch(html, /class="[^"]*\bflex\b/);
    const strip = source('src/components/payments/paymentsUi.jsx');
    // На телефоне — один ряд, который листается вбок (иначе восемь плиток заняли бы пол-экрана).
    assert.ok(strip.includes("const TILES_STRIP = { gridAutoFlow: 'column', gridAutoColumns: `minmax(${TILE_MIN}px, 1fr)` };"));
    assert.ok(strip.includes("style={phone ? TILES_STRIP : tilesWrap(items.length)}"));
});

test('пока счётчики не пришли, ряд плиток стоит заготовкой того же роста', async () => {
    const { StatTilesSkeleton } = await ui();
    const html = renderToStaticMarkup(h(StatTilesSkeleton, { label: 'Загружаем состояния заявок' }));
    assert.match(html, /^<div role="status" aria-busy="true" aria-label="Загружаем состояния заявок" data-stat-tiles="skeleton"/);
    // Та же сетка и те же поля, что у настоящих плиток: плитки встают на место заготовки без сдвига.
    assert.match(html, /style="grid-template-columns:repeat\(auto-fit, minmax\(148px, 1fr\)\);max-width:920px"/);
    assert.equal((html.match(/rounded-xl border border-slate-200 bg-white px-3 py-2\.5/g) || []).length, 4);
    assert.match(html, /<span class="flex h-\[18px\] items-center">/);
    assert.match(html, /<span class="mt-1 flex h-\[22px\] items-center">/);
    assert.doesNotMatch(html, /<button/);
    const view = source('src/components/payments/PaymentsView.jsx');
    // null — счётчики ещё не приходили (заготовка); после ответа или отказа — плитки.
    assert.ok(view.includes('const [counters, setCounters] = useState(entry.registry?.counters || null);'));
    assert.ok(view.includes('{counters ? ('));
    assert.ok(view.includes('<StatTilesSkeleton label="Загружаем состояния заявок" />'));
    assert.ok(view.includes('setCounters((prev) => prev || {});'), 'отказ сервера не оставляет заготовку висеть');
});

test('«Заявки» помнят первую страницу без отбора и открываются ею сразу', () => {
    const view = source('src/components/payments/PaymentsView.jsx');
    assert.ok(view.includes("registry: recall(userId, 'registry'),"));
    assert.ok(view.includes('const [items, setItems] = useState(entry.registry?.items || []);'));
    assert.ok(view.includes('const [total, setTotal] = useState(entry.registry?.total || 0);'));
    // Запоминается только то, с чего вкладка открывается: первая страница без отбора и поиска.
    assert.ok(view.includes('if (!selectionKey && pageNumber === 1) {'));
    assert.ok(view.includes("remember(userId, 'registry', { items: data.items || [], total: Number(data.total) || 0, counters: data.counters || {} });"));
    // В хранилище браузера строки реестра не уходят: в них имена. Только память страницы (paymentsEntry.js).
    const entry = source('src/components/payments/paymentsEntry.js');
    const shell = entry.slice(entry.indexOf('const SHELL_FIELDS = ['), entry.indexOf('];', entry.indexOf('const SHELL_FIELDS = [')));
    assert.doesNotMatch(shell, /registry|items/);
});

test('срезы реестра и имущества — те же, что были отбором', () => {
    assert.deepEqual(STATE_FILTERS.map((item) => item.key), ['all', 'mine', 'watching', 'open', 'clarification', 'overdue', 'done', 'rejected', 'cancelled']);
    const view = source('src/components/payments/PaymentsView.jsx');
    // Состояние без заявок плиткой не рисуется, пока его не выбрали: пустая плитка — шум.
    assert.ok(view.includes(".filter((item) => ['all', 'mine', 'open'].includes(item.key) || counters[item.counter] || state === item.key)"));
    assert.ok(view.includes('<StatTiles'));
    assert.doesNotMatch(view, /rounded-xl bg-slate-100 px-2 py-1\.5/, 'серой полосы-легенды больше нет');
    const assets = source('src/components/payments/PaymentsAssets.jsx');
    assert.ok(assets.includes("{ key: '', label: 'Всё имущество', tone: null, count: counters.all },"));
    assert.ok(assets.includes('.filter((option) => counters[option.value] || filters.status === option.value)'));
    assert.ok(assets.includes("<StatTiles ariaLabel=\"Статус имущества\" value={filters.status} onChange={(key) => set({ status: key || '' })} items={statusTiles} />"));
    // Списка «Все статусы» рядом с плитками нет: один отбор — в одном месте.
    assert.doesNotMatch(assets, /label: 'Все статусы'/);
    assert.doesNotMatch(assets, /ariaLabel="Статус" \/>/);
    assert.deepEqual(Object.keys(ASSET_STATUS_META), ['in_use', 'in_stock', 'repair', 'written_off']);
});

// ── Строки-карточки ───────────────────────────────────────────────────────────

test('строка-карточка: рамка и фон — на самих ячейках, состояние красит строку целиком', async () => {
    const { CARD_ROW, CARD_TABLE, CARD_TH, cardCell } = await ui();
    const base = 'border-y px-3.5 py-2.5 first:rounded-l-xl first:border-l last:rounded-r-xl last:border-r';
    assert.equal(cardCell(), `${base} border-slate-200 bg-white`);
    assert.equal(cardCell(null), `${base} border-slate-200 bg-white`);
    assert.equal(cardCell('overdue'), `${base} border-rose-200 bg-rose-50/80`);
    assert.equal(cardCell('blocked'), `${base} border-amber-200 bg-amber-50/80`);
    assert.equal(cardCell('done'), `${base} border-emerald-100 bg-emerald-50/60`);
    assert.equal(cardCell('rejected'), `${base} border-slate-200 bg-slate-50`);
    assert.equal(cardCell('неизвестный'), `${base} border-slate-200 bg-white`);
    // Фон — прежний (тот же, что был у строк таблицы), рамка — в тон.
    for (const tone of Object.keys(TONE_ROW)) {
        assert.ok(TONE_EDGE[tone], `у состояния ${tone} есть рамка`);
        assert.equal(toneRow(tone), TONE_ROW[tone]);
        assert.equal(toneEdge(tone), TONE_EDGE[tone]);
    }
    assert.equal(toneEdge(null), '');
    // Между строками зазор, у таблицы своей рамки и полосы под шапкой нет.
    assert.match(CARD_TABLE, /\bborder-separate\b/);
    assert.match(CARD_TABLE, /\bborder-spacing-y-1\.5\b/);
    assert.match(CARD_TABLE, /\btable-fixed\b/);
    assert.doesNotMatch(CARD_TH, /\bbg-/);
    assert.match(CARD_ROW, /cursor-pointer/);
    // Тёмный слой перекрашивает утилиты на самом элементе: вложенных селекторов ([&>td]:…) быть не должно.
    assert.doesNotMatch(`${CARD_TABLE} ${CARD_ROW} ${CARD_TH} ${cardCell('done')}`, /\[&/);
});

test('подпись «Завершённые» встаёт на стыке и только на нём', () => {
    const rows = (...statuses) => statuses.map((status, index) => ({ id: index + 1, status }));
    const page = rows('active', 'active', 'done', 'rejected', 'cancelled');
    assert.deepEqual(page.map((_row, index) => closedGroupStart(page, index)), [false, false, true, false, false]);
    // Страница целиком из завершённых либо целиком из рабочих — без подписи.
    const closed = rows('done', 'done', 'cancelled');
    assert.ok(closed.every((_row, index) => !closedGroupStart(closed, index)));
    const open = rows('active', 'active');
    assert.ok(open.every((_row, index) => !closedGroupStart(open, index)));
    assert.equal(closedGroupStart([], 0), false);
    assert.equal(closedGroupStart(null, 3), false);
    assert.equal(closedGroupStart(page, 9), false);
});

test('разделитель группы: подпись, счётчик по запросу и линия до края', async () => {
    const { GroupDivider } = await ui();
    const plain = renderToStaticMarkup(h(GroupDivider, null, 'Завершённые'));
    assert.match(plain, /<span class="shrink-0 text-\[11px\] font-semibold uppercase tracking-wider text-slate-500">Завершённые<\/span>/);
    assert.match(plain, /<span class="h-px min-w-4 grow basis-0 bg-slate-200" aria-hidden="true"><\/span>/);
    assert.equal((plain.match(/<span/g) || []).length, 2);
    const counted = renderToStaticMarkup(h(GroupDivider, { count: 203 }, 'Завершённые'));
    assert.match(counted, />203<\/span>/);
    // Ширина линии — не произвольным классом: такие блоки оболочка телефона делает прокручиваемыми.
    assert.doesNotMatch(plain, /min-w-\[/);
});

test('реестр заявок: таблица строк-карточек с полосой типа, колонки — те же и настраиваются', () => {
    const view = source('src/components/payments/PaymentsView.jsx');
    const table = view.slice(view.indexOf('<table className={CARD_TABLE}'), view.indexOf('</table>', view.indexOf('<table className={CARD_TABLE}')));
    assert.ok(table.length > 1500, 'таблица реестра не найдена');
    assert.ok(table.includes('style={{ minWidth: tableMinWidth }}'));
    assert.ok(table.includes('{visibleColumns.map((column) => <col key={column.key}'));
    assert.ok(table.includes('data-request={request.id}'));
    assert.ok(table.includes('className={`${CARD_ROW} h-[60px]`}'));
    assert.ok(table.includes('${cardCell(tone)} align-middle'));
    // Тип заявки — полосой у левого края первой ячейки, цвет тот же, что на карточках досок; словами — в подсказке.
    assert.ok(table.includes('before:w-[3px] before:rounded-full ${type.bar}'));
    assert.ok(table.includes('title={cell === 0 ? `Тип заявки: ${type.label}` : undefined}'));
    assert.ok(table.includes('const closedStart = closedGroupStart(items, index);'));
    assert.ok(table.includes('<td colSpan={visibleColumns.length} className="p-0"><GroupDivider>Завершённые</GroupDivider></td>'));
    // Главное в строке заметнее остального: название и сумма.
    assert.ok(view.includes('<div className={`truncate font-medium ${text.main}`}>{request.expense_name}</div>'));
    assert.ok(view.includes('<div className={`font-semibold tabular-nums ${text.main}`}>{fmtMoney(netAmount(request))}</div>'));
    // Пустой реестр — своей карточкой, а не пустой таблицей с шапкой.
    assert.ok(view.includes('{items.length > 0 && (\n                            <table className={CARD_TABLE}'));
    // На телефоне заявки идут карточками — и та же подпись стоит на том же стыке.
    const phone = view.slice(view.indexOf('<div className="mt-3 space-y-2 md:hidden">'), view.indexOf('{loading && !items.length && ('));
    assert.ok(phone.length > 800, 'список заявок для телефона не найден');
    assert.ok(phone.includes('{closedGroupStart(items, index) && <GroupDivider>Завершённые</GroupDivider>}'));
    assert.ok(phone.includes('data-request-card={request.id}'));
});

test('имущество: строки-карточки, списанное — приглушённой строкой', () => {
    const assets = source('src/components/payments/PaymentsAssets.jsx');
    assert.ok(assets.includes('<table className={`${CARD_TABLE} min-w-[980px]`} data-assets>'));
    assert.ok(assets.includes("const gone = asset.status === 'written_off';"));
    assert.ok(assets.includes("const cell = `${cardCell(gone ? 'rejected' : null)} align-middle`;"));
    assert.ok(assets.includes('className={`${CARD_ROW} h-[56px]`}'));
    assert.ok(assets.includes('<div className={`truncate font-medium ${main}`}>{asset.name}</div>'));
    assert.ok(assets.includes('setCounters(body.counters || {});'));
});

// ── Справочники ───────────────────────────────────────────────────────────────

test('справочники слева стоят группами под подписями и со значками', async () => {
    const { default: PaymentsDictionaries } = await dictionaries();
    const html = renderToStaticMarkup(h(PaymentsDictionaries, {
        apiBaseUrl: '', headers: () => ({}), users: [], dictionaries: {},
        capabilities: { is_admin: true, can_manage_cards: true, sees_all_requests: true },
    }));
    const nav = html.slice(html.indexOf('<nav aria-label="Справочники"'), html.indexOf('</nav>'));
    const groups = [...nav.matchAll(/data-nav-group="([a-z]+)"/g)].map((match) => match[1]);
    assert.deepEqual(groups, ['pay', 'approval', 'lists', 'rules']);
    const captions = [...nav.matchAll(/uppercase tracking-wider text-slate-500">([^<]+)<\/div>/g)].map((match) => match[1]);
    // У «Настроек» подписи нет: это не справочник, они просто стоят отдельно.
    assert.deepEqual(captions, ['Поставщики и платежи', 'Согласование', 'Списки для заявки']);
    const labels = [...nav.matchAll(/<span class="min-w-0 grow basis-0">([^<]+)<\/span>/g)].map((match) => match[1]);
    // Порядок и названия — как в ТЗ (п. 13): группы только подписывают то, что уже стояло рядом.
    assert.deepEqual(labels, [
        'Поставщики', 'Договоры', 'Реквизиты компаний', 'Банковские реквизиты поставщиков',
        'Банковские карты сотрудников/поставщиков', 'Регулярные платежи', 'Лимиты согласования', 'Маршруты согласования',
        'Категории закупа', 'Категории имущества', 'Проекты', 'Настройки']);
    // Кто в какой группе: рядом стоит то, что относится к одному делу.
    const members = Object.fromEntries(nav.split('data-nav-group="').slice(1).map((part) => [
        part.slice(0, part.indexOf('"')),
        [...part.matchAll(/<span class="min-w-0 grow basis-0">([^<]+)<\/span>/g)].map((match) => match[1]),
    ]));
    assert.deepEqual(members.pay, ['Поставщики', 'Договоры', 'Реквизиты компаний', 'Банковские реквизиты поставщиков',
        'Банковские карты сотрудников/поставщиков', 'Регулярные платежи']);
    assert.deepEqual(members.approval, ['Лимиты согласования', 'Маршруты согласования']);
    assert.deepEqual(members.lists, ['Категории закупа', 'Категории имущества', 'Проекты']);
    assert.deepEqual(members.rules, ['Настройки']);
    assert.equal((nav.match(/<svg/g) || []).length, 12, 'у каждого справочника свой значок');
    assert.equal((nav.match(/aria-current="page"/g) || []).length, 1);
    assert.match(nav, /aria-current="page"[^>]*>\s*<svg[^>]*text-blue-600[^>]*>[\s\S]*?Поставщики<\/span>/);
    // Чего человеку не открыто, того нет и в группе; опустевшая группа не рисуется вовсе.
    const plain = renderToStaticMarkup(h(PaymentsDictionaries, { apiBaseUrl: '', headers: () => ({}), users: [], dictionaries: {}, capabilities: {} }));
    const plainNav = plain.slice(plain.indexOf('<nav aria-label="Справочники"'), plain.indexOf('</nav>'));
    assert.deepEqual([...plainNav.matchAll(/data-nav-group="([a-z]+)"/g)].map((match) => match[1]), ['pay', 'approval', 'lists']);
    assert.doesNotMatch(plainNav, /Настройки|Регулярные платежи|Банковские карты/);
});

const text = (value) => (value && typeof value === 'object' && !React.isValidElement(value) ? value : null);

test('сведения записи разложены по колонкам: главное — первой, остальное — своими', async () => {
    const { dictionaryColumns, dictionaryTableMin } = await dictionaries();
    // Справочники из одного названия колонок не имеют — они стоят списком.
    for (const name of ['categories', 'asset_categories', 'projects', 'settings', 'templates']) {
        assert.equal(dictionaryColumns(name), null, name);
        assert.equal(dictionaryTableMin(dictionaryColumns(name)), 0);
    }
    const fit = { counterparties: 820, contracts: 820, legal_entities: 820, counterparty_accounts: 820, cards: 820, routes: 820, limits: 888 };
    for (const [name, limit] of Object.entries(fit)) {
        const columns = dictionaryColumns(name);
        assert.ok(Array.isArray(columns) && columns.length >= 3, name);
        // Первая колонка — свободной ширины, остальные заданы числом: таблица знает свой нижний предел.
        assert.equal(columns[0].width, undefined, `${name}: первая колонка свободная`);
        assert.ok(columns.slice(1).every((column) => column.width > 0), `${name}: у остальных колонок ширина`);
        assert.equal(new Set(columns.map((column) => column.key)).size, columns.length);
        // В поле 820 px (окно 1280 со свёрнутым меню) таблицы встают целиком; у лимитов колонок больше.
        assert.ok(dictionaryTableMin(columns) <= limit, `${name}: ${dictionaryTableMin(columns)} > ${limit}`);
        assert.ok(columns[0].label, `${name}: первая колонка подписана`);
    }
});

test('поставщик: форма и БИН под названием, категория, договоры и согласующий — колонками', async () => {
    const { dictionaryColumns } = await dictionaries();
    const [name, category, contracts, approver, state] = dictionaryColumns('counterparties');
    const row = { id: 1, name: 'ТОО «Ромашка»', kind: 'too', bin: '123456789012', vat_payer: true, category_name: 'IT и связь', active_contracts: 2, approver_name: 'Иванова Айгерим', approval_limit: 500000, is_active: true };
    assert.deepEqual([name.label, category.label, contracts.label, approver.label, state.label], ['Поставщик', 'Категория', 'Договоры', 'Согласует', '']);
    assert.equal(text(name.cell(row)).main, 'ТОО «Ромашка»');
    assert.equal(text(name.cell(row)).strong, true);
    assert.match(text(name.cell(row)).sub, /БИН 123456789012 · с НДС$/);
    assert.equal(text(category.cell(row)).main, 'IT и связь');
    assert.deepEqual(text(contracts.cell(row)), { main: 'действующих: 2' });
    assert.equal(text(approver.cell(row)).main, 'Иванова Айгерим');
    assert.match(text(approver.cell(row)).sub, /^до 500[\s  ]000/);
    // Чего нет — серым и словами, а не пустой ячейкой.
    const bare = { id: 2, name: 'ИП Тестов', vat_payer: false, is_active: false };
    assert.deepEqual(text(contracts.cell(bare)), { main: 'нет действующего', muted: true });
    assert.deepEqual(text(approver.cell(bare)), { main: 'по общим правилам', muted: true });
    assert.equal(text(approver.cell({ ...row, approval_limit: null })).sub, 'на любую сумму');
    assert.match(text(name.cell(bare)).sub, /без НДС$/);
    // Скрытая запись помечена в последней колонке.
    assert.match(renderToStaticMarkup(state.cell(bare)), />скрыта<\/span>/);
    assert.equal(renderToStaticMarkup(state.cell(row)), '');
});

test('договор, реквизиты, карта, лимит и маршрут — каждое сведение в своей колонке', async () => {
    const { dictionaryColumns } = await dictionaries();
    const cells = (name, row) => Object.fromEntries(dictionaryColumns(name).map((column) => [column.key, column.cell(row)]));

    const contract = cells('contracts', { id: 1, number: 'Д-17', signed_on: '2026-10-01', starts_on: '2026-10-01', ends_on: null, counterparty_name: 'ТОО «Ромашка»', legal_entity_name: 'ТОО «Наша компания»', amount: 6000000, periodicity: 'monthly', has_file: true, status: 'active' });
    assert.equal(contract.number.main, '№Д-17');
    assert.equal(contract.number.sub, 'от 01.10.2026 · файл приложен');
    assert.deepEqual(contract.party, { main: 'ТОО «Ромашка»', sub: 'ТОО «Наша компания»' });
    assert.deepEqual(contract.term, { main: 'бессрочный', sub: 'с 01.10.2026' });
    assert.equal(contract.amount.sub, 'ежемесячно');
    assert.match(renderToStaticMarkup(contract.status), /Действующий/);
    assert.deepEqual(cells('contracts', { id: 2, number: '5', status: 'active' }).amount, { main: 'не указана', muted: true });
    assert.equal(cells('contracts', { id: 2, number: '5', ends_on: '2026-12-31', status: 'active' }).term.main, 'до 31.12.2026');

    const entity = cells('legal_entities', { id: 1, name: 'ТОО «Наша компания»', kind: 'too', bin: '123456789012', vat_payer: true, iik: 'KZ00TEST0000000000', bank_name: 'АО «Тестовый банк»', requisites_text: 'реквизиты', is_active: true });
    assert.deepEqual(entity.account, { main: 'KZ00TEST0000000000', mono: true, sub: 'АО «Тестовый банк»' });
    assert.match(renderToStaticMarkup(entity.state), /Скопировать реквизиты/);
    assert.deepEqual(cells('legal_entities', { id: 2, name: 'ИП Тестов', is_active: true }).account, { main: 'не указан', muted: true });

    const account = cells('counterparty_accounts', { id: 1, counterparty_name: 'ТОО «Ромашка»', iik: 'KZ00TEST0000000000', bank_name: 'АО «Тестовый банк»', bik: 'TESTKZKA', kbe: '17', is_default: true, is_active: true });
    assert.equal(account.party.main, 'ТОО «Ромашка»');
    assert.deepEqual(account.account, { main: 'KZ00TEST0000000000', mono: true, sub: 'АО «Тестовый банк» · БИК TESTKZKA · КБЕ 17' });
    assert.match(renderToStaticMarkup(account.state), /основной/);

    const card = cells('cards', { id: 7, holder_name: 'Петров Данияр', owner_kind: 'employee', note: 'для командировок', mask: '•••• 1234', is_active: true });
    assert.deepEqual(card.holder, { main: 'Петров Данияр', strong: true, sub: 'карта сотрудника' });
    assert.equal(card.note.main, 'для командировок');
    assert.match(renderToStaticMarkup(card.number), /•••• 1234/);
    assert.equal(cells('cards', { id: 8, owner_name: 'ТОО «Ромашка»', owner_kind: 'supplier', mask: '•••• 9', is_active: true }).holder.sub, 'карта поставщика ТОО «Ромашка»');

    const limit = cells('limits', { id: 1, delegate_name: 'Иванова Айгерим', amount_limit: 5000000, all_counterparties: false, counterparty_ids: [1, 2, 3], all_projects: true, number: '15', issued_on: '2026-08-01', ends_on: '2026-12-31', status: 'active' });
    assert.equal(limit.delegate.main, 'Иванова Айгерим');
    assert.equal(limit.delegate.sub, 'любые заявки');
    assert.match(limit.limit.main, /^до 5[\s  ]000[\s  ]000/);
    assert.deepEqual(limit.scope, { main: 'поставщиков: 3', sub: 'все проекты' });
    assert.deepEqual(limit.basis, { main: 'Приказ №15', sub: 'от 01.08.2026 · до 31.12.2026' });
    const open = cells('limits', { id: 2, delegate_name: 'Петров Данияр', all_counterparties: true, all_projects: false, project_ids: [4], status: 'cancelled' });
    assert.deepEqual(open.limit, { main: 'без лимита', muted: true });
    assert.deepEqual(open.scope, { main: 'все поставщики', sub: 'проектов: 1' });
    assert.deepEqual(open.basis, { main: 'бессрочно', muted: true });
    assert.equal(cells('limits', { id: 3, ends_on: '2026-12-31', status: 'active' }).basis.main, 'до 31.12.2026');

    const route = cells('routes', { id: 1, name: 'Пополнения карт', payment_method: 'card', amount_to: 100000, manager_step: 'skip', approver_name: null, is_active: false });
    assert.equal(route.name.main, 'Пополнения карт');
    assert.match(route.name.sub, /сумма до 100[\s  ]000/);
    assert.equal(route.manager.main, 'Не нужен');
    // Этап руководителя не задан — действует общее правило; условий нет — маршрут для любой заявки.
    const common = cells('routes', { id: 2, name: 'Общий', is_active: true });
    assert.equal(common.manager.main, 'По умолчанию');
    assert.equal(common.name.sub, 'любая заявка');
    assert.deepEqual(route.approver, { main: 'любой из утверждающих', muted: true });
    assert.match(renderToStaticMarkup(route.state), />отключён<\/span>/);
});

test('таблица справочника встаёт, только когда помещается в своё поле; иначе — карточки', () => {
    const ui = source('src/components/payments/paymentsUi.jsx');
    const hook = ui.slice(ui.indexOf('export const useFits = '), ui.indexOf('/* Подпись группы с линией'));
    assert.ok(hook.length > 300, 'useFits не найден');
    // Меряется сам блок (сколько ему оставили меню портала и левый список), а не окно; замер — до отрисовки.
    assert.ok(hook.includes('useIsoLayoutEffect(() => {'));
    assert.ok(hook.includes('const read = () => setWidth(node.clientWidth);'));
    assert.ok(hook.includes('new ResizeObserver(read)'));
    assert.ok(hook.includes('return [ref, width === null ? null : width >= minWidth];'));
    const dict = source('src/components/payments/PaymentsDictionaries.jsx');
    assert.ok(dict.includes('const [paneRef, fits] = useFits(dictionaryTableMin(columns));'));
    assert.ok(dict.includes('const asTable = Boolean(columns && fits);'));
    assert.ok(dict.includes('{asTable && pageItems.length > 0 && ('));
    assert.ok(dict.includes('{!asTable && pageItems.length > 0 && ('));
    // Хук стоит до раннего выхода из компонента — иначе порядок хуков зависел бы от данных.
    assert.ok(dict.indexOf('useFits(dictionaryTableMin(columns))') < dict.indexOf('if (!current) return null;'));
    // Своей прокрутки вбок у таблицы справочника нет: не поместилась — карточки.
    const table = dict.slice(dict.indexOf('{asTable && pageItems.length > 0 && ('), dict.indexOf('{!asTable && pageItems.length > 0 && ('));
    assert.doesNotMatch(table, /overflow-x-auto/);
    // Действия в ячейке («Скопировать реквизиты», «Показать номер») запись не открывают.
    assert.ok(table.includes('(event) => event.stopPropagation()'));
    assert.ok(table.includes('<tr key={item.id} data-row={item.id} onClick={() => openEditor(item)} className={`${CARD_ROW} h-[56px]`}>'));
    // С клавиатуры строку открывает кнопка в первой ячейке.
    assert.ok(table.includes('<button type="button" className="block w-full min-w-0 rounded-lg text-left">{cellView(column.cell(item))}</button>'));
});

test('до замера (и на сервере) записи идут карточками; подкатегории — внутри карточки своей категории', async () => {
    const dict = source('src/components/payments/PaymentsDictionaries.jsx');
    assert.ok(dict.includes('? pageItems.map(({ root, children }) => card(root, { children }))'));
    assert.ok(dict.includes(": pageItems.map((item) => card(item))}"));
    const card = dict.slice(dict.indexOf('    const card = (item, { children = [] } = {}) => {'), dict.indexOf('    const cellView = (value) => {'));
    assert.ok(card.length > 800, 'карточка записи не найдена');
    assert.ok(card.includes('data-record={item.id}'));
    assert.ok(card.includes('data-row={entry.id}'));
    assert.ok(card.includes('{children.map((child) => line(child, { inner: true }))}'));
    assert.ok(card.includes('<ChevronRight size={15} className="shrink-0 text-slate-300" aria-hidden="true" />'));
    // Скрытая запись — приглушённой карточкой.
    assert.ok(card.includes("idle(item) ? 'border-slate-200 bg-slate-50' : 'border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.06)]'"));
});

test('регулярные платежи — тем же видом: колонки, строки-карточки, по ширине поля', () => {
    const panel = source('src/components/payments/FixedPaymentsPanel.jsx');
    assert.ok(panel.includes('const TABLE_MIN = 896;'));
    assert.ok(panel.includes('const [paneRef, fits] = useFits(TABLE_MIN);'));
    assert.ok(panel.includes('{fits && rows.length > 0 && ('));
    assert.ok(panel.includes('{!fits && rows.length > 0 && ('));
    assert.ok(panel.includes('<table className={CARD_TABLE} data-templates>'));
    for (const label of ['Платёж', 'Поставщик', 'Ответственный', 'Сумма и срок', 'Заявка']) {
        assert.ok(panel.includes(`>${label}</th>`), `колонка «${label}»`);
    }
    // Строка открывает запись (тем, кто ведёт справочник); ссылка на заявку и меню — сами по себе.
    assert.ok(panel.includes('onClick={canEdit ? () => openEditor(row) : undefined} className={`h-[56px] ${canEdit ? CARD_ROW : \'\'}`}'));
    assert.ok(panel.includes('onClick={(event) => { event.stopPropagation(); onOpenRequest?.(row.open_request_id); }}'));
    assert.ok(panel.includes('<td className={`${cell} !px-1.5`} onClick={(event) => event.stopPropagation()}>'));
    // Приостановленный платёж — приглушённой строкой, а не полупрозрачной.
    assert.ok(panel.includes("const cell = `${cardCell(row.is_active ? null : 'rejected')} align-middle`;"));
    assert.doesNotMatch(panel, /opacity-60/);
});
