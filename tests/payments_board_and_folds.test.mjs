/*
 * «Оплата счетов»: доска во всю колонку раздела и разделы окна заявки «по запросу».
 *
 * Просьбы владельца 13.10.2026.
 * 1. На досках с большим числом колонок остальные «не заметны при первом взгляде»: на
 *    большом мониторе колонки должны стоять все сразу, если помещаются; если нет — должно
 *    быть видно, что есть ещё, и у доски должна быть своя тонкая полоса прокрутки. Причин
 *    было две: раздел держала колонка в 1400 px (на мониторе 2560 px доска всё равно
 *    показывала 4 колонки из 8), а штатная полоса доски лежала под последней карточкой,
 *    на два экрана ниже.
 * 2. «История, ход заявки и т.д. уходили под таб, то есть раскрытые»: окно заявки было
 *    в два с половиной экрана.
 * 3. Первая версия п. 1 вывела доску из узкой колонки шапки на весь экран — «выглядит
 *    вообще не симметрично»: шапка, вкладки и поиск стояли посередине, а доска — от левого
 *    края за правый. Теперь колонка раздела одна на всё и растёт вместе с экраном; метка
 *    «ещё N» осталась. Тогда же: «более тонкий и красивый скролл».
 *
 * Расчёт раскладки доски проверяет tests/payments_meta.test.mjs (boardLayout и соседи),
 * поведение в браузере — проходы стенда. Здесь — то, что видно без браузера: как
 * рисуется свёрнутый и раскрытый раздел (серверный рендер) и что у шапки, списков и
 * доски действительно одни края (по исходнику: на экране в 1500 px разницы между «одной
 * колонкой» и «у каждого блока своя» не видно, она вылезает на широком мониторе и в
 * установленном портале, где у области портала полей нет).
 *
 * Запуск: node --test tests/payments_board_and_folds.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешние react и
// lucide-react не резолвятся. Каталог .cache не отслеживается git.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-folds-render-test/', import.meta.url)
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
    const outPath = join(OUT_DIR, `${name}.mjs`);
    writeFileSync(outPath, result.outputFiles[0].text);
    return import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
}

const h = React.createElement;

test('свёрнутый раздел: название, суть одной строкой, содержимого в разметке нет', async () => {
    const { Fold, FoldGroup } = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const html = renderToStaticMarkup(h(FoldGroup, null,
        h(Fold, { id: 'history', title: 'История', summary: '17 событий · последнее 12.10.2026 14:37', open: false, onToggle: () => {} },
            h('p', null, 'Заявка создана'))));
    assert.match(html, /data-fold="history"/);
    assert.match(html, /aria-expanded="false"/);
    assert.match(html, />История</);
    assert.match(html, /17 событий · последнее 12\.10\.2026 14:37/);
    // Свёрнутое не рисуется вовсе — не спрятано стилем: окно от него не длиннее.
    assert.doesNotMatch(html, /Заявка создана/);
    // Строка — кнопка с названием раздела: раскрывается и мышью, и с клавиатуры.
    assert.match(html, /<h3[^>]*><button type="button"/);
});

test('раскрытый раздел: содержимое на месте, суть в строке не повторяется', async () => {
    const { Fold, FoldGroup } = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const html = renderToStaticMarkup(h(FoldGroup, null,
        h(Fold, { id: 'history', title: 'История', summary: '17 событий · последнее 12.10.2026 14:37', open: true, onToggle: () => {} },
            h('p', null, 'Заявка создана'))));
    assert.match(html, /aria-expanded="true"/);
    assert.match(html, /Заявка создана/);
    assert.doesNotMatch(html, /17 событий/);
    // Кнопка называет читалке, что именно она раскрывает.
    const controls = html.match(/aria-controls="([^"]+)"/);
    assert.ok(controls, 'у строки раздела нет aria-controls');
    assert.ok(html.includes(`id="${controls[1]}"`), 'содержимое раздела не связано со строкой');
});

test('несколько разделов стоят одной карточкой, раскрытый соседних не раскрывает', async () => {
    const { Fold, FoldGroup } = await bundle('src/components/payments/paymentsUi.jsx', 'ui');
    const html = renderToStaticMarkup(h(FoldGroup, null,
        h(Fold, { id: 'route', title: 'Ход заявки', summary: 'Оплата счёта · Бухгалтерия', open: false, onToggle: () => {} }, h('p', null, 'этапы')),
        h(Fold, { id: 'files', title: 'Документы', summary: '2 файла', open: true, onToggle: () => {} }, h('p', null, 'файлы')),
        h(Fold, { id: 'history', title: 'История', summary: '6 событий', open: false, onToggle: () => {} }, h('p', null, 'события'))));
    assert.equal((html.match(/data-fold=/g) || []).length, 3);
    assert.equal((html.match(/rounded-2xl bg-white ring-1 ring-slate-200\/70/g) || []).length, 1);
    assert.match(html, /файлы/);
    assert.doesNotMatch(html, /этапы|события/);
    assert.match(html, /Оплата счёта · Бухгалтерия/);
    assert.match(html, /6 событий/);
    assert.doesNotMatch(html, /2 файла/);
});

test('окно заявки: ход, документы и история стоят свёрнутыми разделами, а не секциями во весь рост', () => {
    const card = source('src/components/payments/PaymentRequestCard.jsx');
    for (const [id, title] of [['route', 'Ход заявки'], ['files', 'Документы'], ['history', 'История']]) {
        assert.ok(card.includes(`<Fold id="${id}" title="${title}"`), `раздел «${title}» не под раскрытием`);
        assert.ok(!card.includes(`<SectionTitle>${title}</SectionTitle>`), `«${title}» снова стоит секцией во весь рост`);
    }
    // Каждая заявка открывается коротко: раскрытое в прошлой — её дело.
    assert.ok(card.includes('setFolds({});'));
    // От инициатора ждут закрывающие документы — «Документы» раскрыты сами.
    assert.ok(card.includes("const foldOpen = (key) => folds[key] ?? (key === 'files' && docsWanted);"));
});

test('шапка, вкладки, списки и доска стоят в одной колонке раздела — с общими краями', () => {
    const view = source('src/components/payments/PaymentsView.jsx');
    const board = source('src/components/payments/PaymentsBoard.jsx');
    const ui = source('src/components/payments/paymentsUi.jsx');
    const column = ui.match(/export const SECTION_COLUMN = '([^']+)';/);
    assert.ok(column, 'колонка раздела не найдена');
    const classes = column[1].split(' ');
    // По центру, растёт вместе с экраном, предел — восемь колонок бухгалтерии по 268 px
    // (8 × 268 + 7 × 10 + поля 2 × 28). Поля на компьютере 28 px: в установленном портале
    // у области портала своих полей нет, а кнопка меню выступает в неё на 16 px.
    for (const name of ['mx-auto', 'w-full', 'max-w-[2270px]', 'px-3', 'sm:px-5', 'lg:px-7']) {
        assert.ok(classes.includes(name), `в колонке раздела нет ${name}`);
    }
    assert.equal(8 * 268 + 7 * 10 + 2 * 28, 2270);
    // Колонка — сам корень раздела: всё, что в нём, получает одни и те же края…
    assert.ok(view.includes('<div className={`${SECTION_COLUMN} min-h-screen bg-gray-50 pb-12 pt-3 md:min-h-0`} style={{ fontFamily: APPLE_FONT }}>'));
    assert.equal(view.split('SECTION_COLUMN').length - 1, 2, 'колонку раздела берут не только в корне');
    // …и ни вкладка, ни доска своей ширины и своих полей не заводят.
    for (const tab of ['desk', 'requests', 'assets', 'dictionaries', 'roles']) {
        const at = view.indexOf(`{activeTab === '${tab}' && (`);
        assert.ok(at > 0, `вкладка ${tab} не найдена`);
        assert.ok(view.slice(at, at + 160).includes('<div className="mt-4 motion-safe:animate-fade-in-soft">'), `у вкладки ${tab} своя ширина или свои поля`);
    }
    const boardAt = view.indexOf('{boardCode && meta && (');
    assert.ok(boardAt > 0);
    assert.ok(view.slice(boardAt, boardAt + 160).includes('<div key={boardCode} className="mt-4 motion-safe:animate-fade-in-soft">'), 'у доски своя ширина или свои поля');
    // Доска из колонки не выходит: своей колонки нет, поле доски — без полей и отступов наружу.
    assert.ok(!board.includes('SECTION_COLUMN'));
    assert.ok(board.includes('<div className="relative mt-2" data-board-area '));
    // Метки скрытых колонок стоят у краёв поля, то есть у краёв шапки.
    assert.ok(board.includes("className={`pointer-events-none absolute inset-y-0 z-10 ${right ? 'right-0' : 'left-0'}`}"));
});

test('у доски своя тонкая полоса и метки скрытых колонок, штатной полосы нет', () => {
    const board = source('src/components/payments/PaymentsBoard.jsx');
    const view = source('src/components/payments/PaymentsView.jsx');
    // Штатная полоса лежала под последней карточкой — её работу делает своя, прижатая к низу экрана
    // (поле области портала снимается замером: во вкладке оно 32 px, в установленном портале — ноль).
    assert.ok(board.includes('[scrollbar-width:none] [&::-webkit-scrollbar]:hidden'));
    assert.ok(board.includes('role="scrollbar"'));
    assert.match(board, /<div data-board-scrollbar className="pointer-events-none sticky z-10[^"]*" style=\{\{ bottom: -inset \}\}>/);
    // Тонкая: три пикселя, толще — только под указателем и пока её тянут.
    assert.ok(board.includes("${held ? 'h-1.5' : 'h-[3px]'}"));
    assert.ok(board.includes('transition-[height] duration-150 group-hover:h-1.5 motion-reduce:transition-none'));
    // Лежит на дорожке цвета страницы, карточки уходят под неё, угасая: тонкая линия прямо по
    // карточкам зачёркивала в них строку. Дорожка — тем же классом, что фон раздела (тёмная тема
    // перекрашивает их вместе), угасание — маской.
    assert.ok(view.includes('min-h-screen bg-gray-50 pb-12'));
    assert.ok(board.includes('<div aria-hidden="true" className="h-2.5 bg-gray-50" style={RAIL_FADE} />'));
    assert.ok(board.includes('const RAIL_FADE = { maskImage: RAIL_FADE_IMAGE, WebkitMaskImage: RAIL_FADE_IMAGE };'));
    // Ни разделителя, ни рамки, ни тени: первая версия лежала серой лентой под чертой.
    const rail = board.slice(board.indexOf('<div data-board-scrollbar'), board.indexOf('/* Поле доски на компьютере'));
    assert.ok(rail.length > 400, 'разметка полосы не найдена');
    assert.doesNotMatch(rail, /border|ring-|shadow/);
    // Угасающая часть дорожки нажатий не перехватывает — под ней ещё видны карточки; сплошная
    // перехватывает целиком: иначе нажатие у низа экрана открывало бы карточку, которой не видно.
    assert.ok(rail.includes('className="group pointer-events-auto flex h-4 cursor-pointer touch-none items-center bg-gray-50 px-1"'));
    assert.equal(rail.split('pointer-events-auto').length - 1, 1);
    assert.equal(rail.split('bg-gray-50').length - 1, 2);
    // Метки у обоих краёв; гаснущий край — маской, чтобы не зависеть от цвета темы.
    assert.ok(board.includes('<BoardMore side="left" columns={hidden.left}'));
    assert.ok(board.includes('<BoardMore side="right" columns={hidden.right}'));
    assert.ok(board.includes('return { maskImage: image, WebkitMaskImage: image };'));
    // Колонка берёт ширину из раскладки, а не из класса: иначе экран снова делился бы по 268 px.
    assert.ok(board.includes('style={single ? undefined : { width }}'));
    assert.doesNotMatch(board, /w-\[268px\]/);
});
