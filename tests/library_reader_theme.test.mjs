// «Библиотека» (#405): тема страниц ридера — день или ночь (чёрные страницы,
// белый текст), выбор читателя на устройстве, по умолчанию — тема портала.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { PALETTES, ReaderEngine } from '../src/components/library/readerEngine.js';
import {
    READER_THEME_KEY, initialReaderNight, portalIsDark, readReaderNight, writeReaderNight,
} from '../src/components/library/readerTheme.js';

// Ридер — JSX: собираем его так же, как tests/ai_qa_adjudication_card.test.mjs.
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const readerFile = join(cache, 'LibraryReader.cjs');
buildSync({ entryPoints: ['src/components/library/LibraryReader.jsx'], outfile: readerFile,
    bundle: true, platform: 'node', format: 'cjs', packages: 'external', loader: { '.js': 'jsx' } });
const { CHROME, LibraryToc } = createRequire(import.meta.url)(readerFile);

const memoryStorage = (initial = {}) => {
    const data = { ...initial };
    return {
        data,
        getItem: (key) => (key in data ? data[key] : null),
        setItem: (key, value) => { data[key] = String(value); },
    };
};
const brokenStorage = {
    getItem() { throw new Error('SecurityError'); },
    setItem() { throw new Error('QuotaExceededError'); },
};
const portal = (theme) => ({ getAttribute: (name) => (name === 'data-otp-theme' ? theme : null) });

test('выбор читателя читается как день, ночь или «не выбирал»', () => {
    assert.equal(readReaderNight(memoryStorage({ [READER_THEME_KEY]: 'night' })), true);
    assert.equal(readReaderNight(memoryStorage({ [READER_THEME_KEY]: 'day' })), false);
    assert.equal(readReaderNight(memoryStorage()), null);
    assert.equal(readReaderNight(memoryStorage({ [READER_THEME_KEY]: 'dark' })), null);
    assert.equal(readReaderNight(null), null);
    // Приватный режим Safari бросает на любом обращении — ридер всё равно открывается.
    assert.equal(readReaderNight(brokenStorage), null);
});

test('выбор запоминается и не роняет ридер, если хранилище недоступно', () => {
    const storage = memoryStorage();
    writeReaderNight(true, storage);
    assert.equal(storage.data[READER_THEME_KEY], 'night');
    writeReaderNight(false, storage);
    assert.equal(storage.data[READER_THEME_KEY], 'day');
    assert.doesNotThrow(() => writeReaderNight(true, brokenStorage));
    assert.doesNotThrow(() => writeReaderNight(true, null));
});

test('пока читатель не выбирал, книга следует теме портала; его выбор главнее', () => {
    assert.equal(portalIsDark(portal('dark')), true);
    assert.equal(portalIsDark(portal(null)), false);
    assert.equal(portalIsDark(null), false);
    assert.equal(initialReaderNight(memoryStorage(), portal('dark')), true);
    assert.equal(initialReaderNight(memoryStorage(), portal(null)), false);
    assert.equal(initialReaderNight(memoryStorage({ [READER_THEME_KEY]: 'day' }), portal('dark')), false);
    assert.equal(initialReaderNight(memoryStorage({ [READER_THEME_KEY]: 'night' }), portal(null)), true);
    assert.equal(initialReaderNight(brokenStorage, portal('dark')), true);
});

const luminance = (hex) => {
    const value = hex.replace('#', '');
    const [r, g, b] = [0, 2, 4].map((at) => parseInt(value.slice(at, at + 2), 16) / 255)
        .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a, b) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
};

test('ночь — чёрные страницы и белый текст (так в задаче)', () => {
    const { night } = PALETTES;
    assert.equal(night.paper, '#000000');
    assert.equal(night.night, true);
    assert.ok(luminance(night.ink) > 0.8, `текст ${night.ink} должен быть белым`);
    assert.ok(contrast(night.ink, night.paper) >= 15);
    // Подписи и номера страниц читаются, но тише текста.
    assert.ok(contrast(night.soft, night.paper) >= 4.5);
    assert.ok(luminance(night.soft) < luminance(night.ink));
    // Оборот листа и стол отличимы от страницы — иначе загиб и край книги не видны.
    assert.notEqual(night.back, night.paper);
    assert.notEqual(night.desk, night.paper);
});

test('у дня и ночи одни и те же краски: движок перекрашивает книгу на ходу', () => {
    assert.deepEqual(Object.keys(PALETTES.night).sort(), Object.keys(PALETTES.day).sort());
    for (const palette of Object.values(PALETTES)) {
        assert.equal(palette.spine.length, 4);
    }
    assert.equal(PALETTES.day.night, false);
});

test('setPalette перекрашивает все страницы, замерщик, оборот листа и стопки — без перевёрстки', () => {
    const painted = [];
    const view = (name) => ({ applyPalette: (palette) => painted.push([name, palette]) });
    const engine = {
        palette: PALETTES.day,
        views: [view('a'), view('b'), view('c'), view('d')],
        measurer: view('measurer'),
        flapPaper: { style: {} },
        books: 0,
        paintBook() { this.books += 1; },
        layout() { throw new Error('перевёрстка при смене темы'); },
        startPagination() { throw new Error('пересчёт номеров при смене темы'); },
        openAt() { throw new Error('переход при смене темы'); },
    };
    ReaderEngine.prototype.setPalette.call(engine, PALETTES.night);
    assert.deepEqual(painted.map(([name]) => name).sort(), ['a', 'b', 'c', 'd', 'measurer']);
    assert.ok(painted.every(([, palette]) => palette === PALETTES.night));
    assert.equal(engine.flapPaper.style.background, PALETTES.night.back);
    assert.equal(engine.books, 1);
    assert.equal(engine.palette, PALETTES.night);
    // Та же тема ещё раз — ничего не перекрашивает.
    ReaderEngine.prototype.setPalette.call(engine, PALETTES.night);
    assert.equal(painted.length, 5);
    assert.equal(engine.books, 1);
});

const rgb = (color) => {
    const hex = /^#([0-9a-f]{6})$/i.exec(color);
    if (hex) return [0, 2, 4].map((at) => parseInt(hex[1].slice(at, at + 2), 16)).concat(1);
    const [r, g, b, a = 1] = /rgba?\(([^)]+)\)/.exec(color)[1].split(',').map(Number);
    return [r, g, b, a];
};
const over = (top, bottom) => {
    const [r, g, b, a] = rgb(top);
    const [br, bg, bb] = rgb(bottom);
    const mix = (x, y) => Math.round(x * a + y * (1 - a));
    return `#${[mix(r, br), mix(g, bg), mix(b, bb)].map((x) => x.toString(16).padStart(2, '0')).join('')}`;
};

test('ночью ссылки, рамки, стопки и корешок видны на чёрной бумаге', () => {
    const { night, day } = PALETTES;
    assert.ok(contrast(night.link, night.paper) >= 4.5);
    for (const key of ['rule', 'edge', 'back']) assert.notEqual(night[key], night.paper, key);
    // Корешок ночью — отсвет (светлый), днём — тень (тёмная).
    assert.match(night.spine[0], /^rgba\(255,255,255,0\.\d+\)$/);
    assert.match(day.spine[0], /^rgba\(0,0,0,0\.\d+\)$/);
    assert.notEqual(over(night.spine[0], night.paper), night.paper);
});

test('рамка ридера ночью читается: шапка, подвал, оглавление, «Вернуться», «Закончено»', () => {
    const { night } = CHROME;
    const paper = PALETTES.night.paper;
    const desk = PALETTES.night.desk;
    const panel = over(night.panel, paper);
    assert.ok(contrast(night.text, paper) >= 4.5);
    assert.ok(contrast(night.text, desk) >= 4.5);
    assert.ok(contrast(night.done, paper) >= 4.5);
    assert.ok(contrast('#ffffff', over(night.pill, paper)) >= 7);
    // Плашка «Вернуться на стр. N» отличима от чёрной страницы, а не только её буквы.
    assert.ok(contrast(over(night.pill, paper), paper) >= 1.5);
    for (const key of ['title', 'item']) assert.ok(contrast(night[key], panel) >= 7, key);
    assert.ok(contrast(night.current, panel) >= 4.5);
    assert.ok(contrast(night.button.color, night.button.background) >= 7);
});

test('днём рамка ридера та же, что до тёмной темы', () => {
    assert.deepEqual(
        { ...CHROME.day, button: { ...CHROME.day.button } },
        {
            text: '#8b857b', done: '#059669', pill: 'rgba(15, 23, 42, 0.80)', hover: 'hover:bg-black/[0.05]',
            panel: 'rgba(255, 255, 255, 0.95)', title: '#0f172a', item: '#1e293b', secondary: '#64748b',
            tertiary: '#94a3b8', current: '#2563eb', currentNumber: '#3b82f6', handle: '#cbd5e1',
            line: 'border-black/[0.06]', row: 'border-black/[0.05]',
            rowHover: 'hover:bg-black/[0.03] active:bg-black/[0.06]', close: 'bg-black/[0.05] hover:bg-black/[0.09]',
            button: { background: '#f1f5f9', color: '#475569' }, buttonHover: 'hover:brightness-95',
        },
    );
    assert.deepEqual(Object.keys(CHROME.night).sort(), Object.keys(CHROME.day).sort());
});

const toc = (chrome) => renderToStaticMarkup(React.createElement(LibraryToc, {
    narrow: true, chrome, book: { title: 'Ночная книга', author: 'Автор' },
    toc: [{ href: 'a', title: 'Глава первая', depth: 0 }, { href: 'b', title: 'Глава вторая', depth: 0 }],
    pages: [1, 7], folio: 3, total: 26, currentIndex: 1, onSelect() {}, onClose() {},
}));

test('оглавление красится темой книги, а не классами, которые перекрашивает тёмный портал', () => {
    const night = toc(CHROME.night);
    const day = toc(CHROME.day);
    assert.match(night, /background:rgba\(28, 28, 30, 0\.95\)/);
    assert.match(night, /color:#f2f2f7[^>]*>Ночная книга</);
    assert.match(night, /color:#0a84ff[^>]*>.*Глава вторая/);
    assert.match(night, /color:#e5e5ea[^>]*>.*Глава первая/);
    assert.match(night, /background:#48484a/);
    assert.match(day, /background:rgba\(255, 255, 255, 0\.95\)/);
    assert.match(day, /color:#0f172a[^>]*>Ночная книга</);
    for (const html of [night, day]) {
        assert.doesNotMatch(html, /\b(?:text|bg|border|ring)-(?:slate|gray|blue|emerald)-\d/);
    }
});
