// «Библиотека» (#282): счёт страниц и процентов ридера.
// Таблица PAGE_CASES — та же, что в tests/test_library.py: номер страницы в
// оглавлении считает сервер, внизу экрана — ридер, и расходиться им нельзя.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    LIBRARY_TABS, STATUS_LABELS, currentTocIndex, filterBooks, formatActivity, formatDay, formatPercent,
    formatPosition, isEpubFile, pageAtFraction, pageForOffset, parsePosition, progressFromPosition,
} from '../src/components/library/libraryMeta.js';

const PAGE_CASES = [
    [0, 5000, 1],
    [1799, 5000, 1],
    [1800, 5000, 2],
    [4999, 5000, 3],
    [5000, 5000, 3],
    [99999, 5000, 3],
    [-5, 5000, 1],
    [0, 0, 1],
];

test('страница — 1800 знаков, как на сервере', () => {
    for (const [offset, total, page] of PAGE_CASES) {
        assert.equal(pageForOffset(offset, total, 1800), page, `${offset}/${total}`);
    }
});

const BOOK = {
    chars_per_page: 1800,
    total_chars: 9000,
    total_pages: 5,
    spine: [
        { start: 0, chars: 3600 },
        { start: 3600, chars: 0 },       // нелинейная глава
        { start: 3600, chars: 5400 },
    ],
};

const at = (section, fraction, extra = {}) => ({ section, fraction, atEnd: false, paged: false, ...extra });

test('место в книге: начало главы плюс доля пролистанного', () => {
    assert.deepEqual(progressFromPosition(at(0, 0), BOOK), { offset: 0, percent: 0, page: 1, atEnd: false });
    // Половина первой главы: 3600 * 0.5 = 1800 знаков.
    assert.deepEqual(progressFromPosition(at(0, 0.5), BOOK), { offset: 1800, percent: 20, page: 2, atEnd: false });
    // Вторая линейная глава, начало: 3600.
    assert.deepEqual(progressFromPosition(at(2, 0), BOOK), { offset: 3600, percent: 40, page: 3, atEnd: false });
});

test('100 % ставит только последняя страница, до которой долистали', () => {
    const last = progressFromPosition(at(2, 0.95, { atEnd: true }), BOOK);
    assert.equal(last.atEnd, false, 'конец без листания — прыжок, не финиш');
    assert.ok(last.percent < 100);
    assert.deepEqual(progressFromPosition(at(2, 0.95, { atEnd: true, paged: true }), BOOK),
        { offset: 9000, percent: 100, page: 5, atEnd: true });
});

test('кривые данные не ломают счёт', () => {
    assert.equal(progressFromPosition(null, BOOK), null);
    assert.equal(progressFromPosition({ section: -1, fraction: 0 }, BOOK), null);
    assert.deepEqual(progressFromPosition(at(99, 0.3), BOOK), { offset: 0, percent: 0, page: 1, atEnd: false });
    assert.equal(progressFromPosition(at(0, 7), BOOK).page, 3);
});

test('место для сервера — «глава:доля» в формате ручки', () => {
    assert.equal(formatPosition(12, 0.4375), '12:0.4375');
    assert.equal(formatPosition(3, 0), '3:0');
    assert.equal(formatPosition(3, 1 / 3), '3:0.333333');
    assert.equal(formatPosition(3, 1), '3:0.999999');
    const serverPattern = /^(\d{1,5}):(0(?:\.\d{1,6})?|1(?:\.0{1,6})?)$/;
    for (const value of [formatPosition(0, 0), formatPosition(40, 0.99999), formatPosition(7, 0.5)]) {
        assert.match(value, serverPattern);
    }
    assert.deepEqual(parsePosition('12:0.4375'), { section: 12, fraction: 0.4375 });
    assert.equal(parsePosition('epubcfi(/6/4)'), null);
    assert.equal(parsePosition(''), null);
});

test('процент на карточке', () => {
    assert.equal(formatPercent(0), '0 %');
    assert.equal(formatPercent(0.4), '<1 %');
    assert.equal(formatPercent(45.9), '45 %');
    assert.equal(formatPercent(99.9), '99 %');
    assert.equal(formatPercent(100), '100 %');
    assert.equal(formatPercent(250), '100 %');
});

test('текущий пункт оглавления', () => {
    const toc = [{ page: 1 }, { page: 3 }, { page: 3 }, { page: 10 }];
    assert.equal(currentTocIndex(toc, 1), 0);
    assert.equal(currentTocIndex(toc, 3), 2);
    assert.equal(currentTocIndex(toc, 9), 2);
    assert.equal(currentTocIndex(toc, 12), 3);
    assert.equal(currentTocIndex([{ page: 2 }], 1), -1);
    assert.equal(currentTocIndex(null, 1), -1);
});

test('текущая глава — по смещению: на первой странице их бывает несколько', () => {
    const toc = [
        { page: 1, offset: 0 },     // титул
        { page: 1, offset: 120 },   // содержание
        { page: 1, offset: 900 },   // глава 1
        { page: 2, offset: 2400 },
    ];
    assert.equal(currentTocIndex(toc, 1, 0), 0);
    assert.equal(currentTocIndex(toc, 1, 500), 1);
    assert.equal(currentTocIndex(toc, 1, 1000), 2);
    assert.equal(currentTocIndex(toc, 2, 2400), 3);
    // Без смещения у пунктов (старое оглавление) — по странице, как раньше.
    assert.equal(currentTocIndex([{ page: 1 }, { page: 1 }], 1, 0), 1);
});

test('оглавление не по порядку текста не теряет главу', () => {
    const toc = [
        { title: 'Аннотация', page: 267, offset: 480000 },
        { title: 'Глава 1', page: 2, offset: 2000 },
        { title: 'Глава 2', page: 23, offset: 40000 },
    ];
    assert.equal(currentTocIndex(toc, 26, 45000), 2);
    assert.equal(currentTocIndex(toc, 2, 2500), 1);
    assert.equal(currentTocIndex(toc, 268, 481000), 0);
    assert.equal(currentTocIndex(toc, 1, 10), -1);
});

test('вкладки: «Сохранённые» — фильтр того же списка', () => {
    const books = [{ id: 1, saved: true }, { id: 2, saved: false }];
    assert.deepEqual(filterBooks(books, LIBRARY_TABS.all).map((b) => b.id), [1, 2]);
    assert.deepEqual(filterBooks(books, LIBRARY_TABS.saved).map((b) => b.id), [1]);
});

test('подписи статусов — дословно из ТЗ', () => {
    assert.deepEqual(Object.values(STATUS_LABELS), ['Не начато', 'В процессе', 'Закончено']);
});

test('дата активности — часы Алматы как есть, без перевода пояса', () => {
    assert.equal(formatActivity('2026-09-28T23:40:05'), '28.09.2026, 23:40');
    assert.equal(formatActivity(null), '—');
    // «Начал» и «Закончено» — днём, без перевода пояса: 23:40 остаётся 28-м.
    assert.equal(formatDay('2026-09-28T23:40:05'), '28.09.2026');
    assert.equal(formatDay(''), '—');
});

test('только .epub', () => {
    assert.equal(isEpubFile({ name: 'Война и мир.EPUB' }), true);
    assert.equal(isEpubFile({ name: 'книга.pdf' }), false);
    assert.equal(isEpubFile(null), false);
});

test('сохранённое место открывается на той же странице', () => {
    // Каждая страница любой главы до 3000 экранов: начало экрана → строка
    // сервера → обратно — та же страница.
    for (const pages of [1, 7, 60, 377, 3000]) {
        for (let page = 0; page < pages; page += 1) {
            const saved = parsePosition(formatPosition(4, page / pages));
            assert.equal(pageAtFraction(saved.fraction, pages), page, `${page} из ${pages}`);
        }
    }
    // Середина экрана (перекладка при смене размера) — тоже на своей странице.
    assert.equal(pageAtFraction(10.5 / 60, 60), 10);
    assert.equal(pageAtFraction(1, 60), 59);
});
