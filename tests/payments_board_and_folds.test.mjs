/*
 * «Оплата счетов»: доска на всю ширину экрана и разделы окна заявки «по запросу».
 *
 * Просьба владельца 13.10.2026, две части.
 * 1. На досках с большим числом колонок остальные «не заметны при первом взгляде»: на
 *    большом мониторе колонки должны стоять все сразу, если помещаются; если нет — должно
 *    быть видно, что есть ещё, и у доски должна быть своя тонкая полоса прокрутки. Причин
 *    было две: раздел держала колонка в 1400 px (на мониторе 2560 px доска всё равно
 *    показывала 4 колонки из 8), а штатная полоса доски лежала под последней карточкой,
 *    на два экрана ниже.
 * 2. «История, ход заявки и т.д. уходили под таб, то есть раскрытые»: окно заявки было
 *    в два с половиной экрана.
 *
 * Расчёт раскладки доски проверяет tests/payments_meta.test.mjs (boardLayout и соседи),
 * поведение в браузере — проходы стенда. Здесь — то, что видно без браузера: как
 * рисуется свёрнутый и раскрытый раздел (серверный рендер) и что доска действительно
 * стоит вне колонки раздела (по исходнику: такую правку легко «починить обратно», не
 * заметив на своём экране в 1500 px никакой разницы).
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

test('доска стоит вне колонки раздела — на всю ширину экрана', () => {
    const view = source('src/components/payments/PaymentsView.jsx');
    const board = source('src/components/payments/PaymentsBoard.jsx');
    const ui = source('src/components/payments/paymentsUi.jsx');
    assert.ok(ui.includes("export const SECTION_COLUMN = 'mx-auto max-w-[1400px] px-3 sm:px-5';"));
    // Корень раздела ширину не ограничивает: это делает каждый блок сам…
    const root = view.match(/<div className="([^"]*)" style=\{\{ fontFamily: APPLE_FONT \}\}>/);
    assert.ok(root, 'корень раздела не найден');
    assert.doesNotMatch(root[1], /max-w-|mx-auto|\bpx-/);
    // …все, кроме доски.
    for (const tab of ['desk', 'requests', 'assets', 'dictionaries', 'roles']) {
        const at = view.indexOf(`{activeTab === '${tab}' && (`);
        assert.ok(at > 0, `вкладка ${tab} не найдена`);
        assert.ok(view.slice(at, at + 160).includes('<div className={`${SECTION_COLUMN} mt-4'), `вкладка ${tab} осталась без колонки раздела`);
    }
    const boardAt = view.indexOf('{boardCode && meta && (');
    assert.ok(boardAt > 0);
    assert.ok(!view.slice(boardAt, boardAt + 160).includes('SECTION_COLUMN'), 'доску снова заперли в колонку раздела');
    // В самой доске колонка раздела держит только панель поиска и фильтров; поле доски — после неё.
    const columnAt = board.indexOf('<div className={SECTION_COLUMN}>');
    const lanesAt = board.indexOf('{!isMobile && <BoardLanes ');
    const filtersAt = board.indexOf('<PaymentsFilters ');
    assert.ok(columnAt > 0 && filtersAt > columnAt && lanesAt > filtersAt);
    assert.equal(board.slice(columnAt, lanesAt).split('<div className={SECTION_COLUMN}>').length, 2);
});

test('у доски своя тонкая полоса и метки скрытых колонок, штатной полосы нет', () => {
    const board = source('src/components/payments/PaymentsBoard.jsx');
    // Штатная полоса лежала под последней карточкой — её работу делает своя, прижатая к низу экрана.
    assert.ok(board.includes('[scrollbar-width:none] [&::-webkit-scrollbar]:hidden'));
    assert.ok(board.includes('data-board-scrollbar'));
    assert.ok(board.includes('role="scrollbar"'));
    assert.match(board, /className="group sticky z-10[^"]*"\s+style=\{\{ bottom: -inset \}\}/);
    // Метки у обоих краёв; гаснущий край — маской, чтобы не зависеть от цвета темы.
    assert.ok(board.includes('<BoardMore side="left" columns={hidden.left}'));
    assert.ok(board.includes('<BoardMore side="right" columns={hidden.right}'));
    assert.ok(board.includes('return { maskImage: image, WebkitMaskImage: image };'));
    // Колонка берёт ширину из раскладки, а не из класса: иначе экран снова делился бы по 268 px.
    assert.ok(board.includes('style={single ? undefined : { width }}'));
    assert.doesNotMatch(board, /w-\[268px\]/);
});
