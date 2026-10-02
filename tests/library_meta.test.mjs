// «Библиотека» (#282): счёт страниц и процентов ридера.
// Таблица PAGE_CASES — та же, что в tests/test_library.py: номер страницы в
// оглавлении считает сервер, внизу экрана — ридер, и расходиться им нельзя.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    LIBRARY_TABS, STATUS_LABELS, bookInDepartment, bookInGenre, currentTocIndex, filterBooks, findGenreByName,
    formatActivity, formatBookCount, formatDay, formatPercent, formatPosition, genreBookCounts, genresOnShelf,
    isEpubFile, normalizeGenreName, pageAtFraction, pageForOffset, parsePosition, progressFromPosition,
    restoreDepartment, shelfCountByDepartment, sortGenres,
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

/* Отделы и архив (29.09.2026): каталог приходит один раз, отдел и вкладка
   делят его на месте. */
const SHELF = [
    { id: 1, department_ids: [1, 3], saved: true, archived: false },
    { id: 2, department_ids: [3], saved: false, archived: false },
    { id: 3, department_ids: [1], saved: true, archived: true },
    { id: 4, department_ids: [], saved: false, archived: false },
];

test('отдел: его книги, «Все отделы» — все', () => {
    const ids = (tab, dept) => filterBooks(SHELF, tab, dept).map((b) => b.id);
    assert.deepEqual(ids(LIBRARY_TABS.all, ''), [1, 2, 4]);
    assert.deepEqual(ids(LIBRARY_TABS.all, 1), [1]);
    assert.deepEqual(ids(LIBRARY_TABS.all, '3'), [1, 2]);
    assert.deepEqual(ids(LIBRARY_TABS.all, 99), []);
});

test('архив — отдельная вкладка, в «Общем доступе» и «Сохранённых» его нет', () => {
    const ids = (tab, dept) => filterBooks(SHELF, tab, dept).map((b) => b.id);
    assert.deepEqual(ids(LIBRARY_TABS.archive, ''), [3]);
    assert.deepEqual(ids(LIBRARY_TABS.archive, 3), []);
    assert.deepEqual(ids(LIBRARY_TABS.saved, ''), [1]);
    assert.deepEqual(ids(LIBRARY_TABS.saved, 1), [1]);
});

test('книга без отделов видна только во «Всех отделах»', () => {
    assert.equal(bookInDepartment(SHELF[3], ''), true);
    assert.equal(bookInDepartment(SHELF[3], null), true);
    assert.equal(bookInDepartment(SHELF[3], 1), false);
    assert.equal(bookInDepartment({}, 1), false);
});

test('счётчик отдела — книги на полке, книга двух отделов считается у обоих', () => {
    const counts = shelfCountByDepartment(SHELF);
    assert.equal(counts.get(1), 1);
    assert.equal(counts.get(3), 2);
    assert.equal(counts.has(99), false);
});

test('запомненный отдел: только из списка, иначе «Все отделы»', () => {
    const departments = [{ id: 1 }, { id: 3 }];
    assert.equal(restoreDepartment('3', departments), 3);
    assert.equal(restoreDepartment('7', departments), '');
    assert.equal(restoreDepartment('', departments), '');
    assert.equal(restoreDepartment('abc', departments), '');
    assert.equal(restoreDepartment('-1', departments), '');
    assert.equal(restoreDepartment('3', null), '');
});

test('запомненный отдел, которому книги больше не выдаются, — «Все отделы»', () => {
    const departments = [{ id: 1, active: true }, { id: 70, active: false }];
    assert.equal(restoreDepartment('1', departments), 1);
    assert.equal(restoreDepartment('70', departments), '');
});

/* Жанры (02.10.2026): у книги их сколько угодно, полка делится строкой жанров. */
const GENRE_SHELF = [
    { id: 1, department_ids: [1], genre_ids: [10, 20], saved: true, archived: false },
    { id: 2, department_ids: [1], genre_ids: [20], saved: false, archived: false },
    { id: 3, department_ids: [3], genre_ids: [30], saved: false, archived: true },
    { id: 4, department_ids: [3], saved: false, archived: false },
];
const GENRES = [
    { id: 20, name: 'психология' }, { id: 10, name: 'Бизнес' }, { id: 30, name: 'Ёмкие истории' },
    { id: 40, name: 'Пустой' }, { id: 50, name: 'Европа' },
];

test('жанр: его книги, «Все жанры» — все; книга без жанра — только во «Всех»', () => {
    const ids = (tab, dept, genre) => filterBooks(GENRE_SHELF, tab, dept, genre).map((b) => b.id);
    assert.deepEqual(ids(LIBRARY_TABS.all, '', ''), [1, 2, 4]);
    assert.deepEqual(ids(LIBRARY_TABS.all, '', 20), [1, 2]);
    assert.deepEqual(ids(LIBRARY_TABS.all, '', '10'), [1]);
    assert.deepEqual(ids(LIBRARY_TABS.saved, '', 20), [1]);
    assert.deepEqual(ids(LIBRARY_TABS.archive, '', 30), [3]);
    assert.deepEqual(ids(LIBRARY_TABS.all, 3, 20), []);
    assert.equal(bookInGenre(GENRE_SHELF[3], null), true);
    assert.equal(bookInGenre(GENRE_SHELF[3], 10), false);
});

test('строка жанров — только жанры с книгами на этой полке, по алфавиту', () => {
    const shelf = filterBooks(GENRE_SHELF, LIBRARY_TABS.all, '');
    assert.deepEqual(genresOnShelf(GENRES, shelf).map((g) => g.id), [10, 20]);
    const archive = filterBooks(GENRE_SHELF, LIBRARY_TABS.archive, '');
    assert.deepEqual(genresOnShelf(GENRES, archive).map((g) => g.id), [30]);
    assert.deepEqual(genresOnShelf(GENRES, []), []);
    assert.deepEqual(genresOnShelf(null, shelf), []);
});

test('жанры сортируются по-русски без учёта регистра, «Ё» — после «Е»', () => {
    assert.deepEqual(sortGenres(GENRES).map((g) => g.name), ['Бизнес', 'Европа', 'Ёмкие истории', 'психология', 'Пустой']);
    // Исходный список не меняется — он состояние React.
    assert.equal(GENRES[0].id, 20);
});

test('счётчик жанра — книга нескольких жанров считается у каждого, архив тоже', () => {
    const counts = genreBookCounts(GENRE_SHELF);
    assert.equal(counts.get(20), 2);
    assert.equal(counts.get(10), 1);
    assert.equal(counts.get(30), 1);
    assert.equal(counts.has(40), false);
});

test('имя жанра: пробелы схлопнуты, регистр при поиске дубля не важен', () => {
    assert.equal(normalizeGenreName('  Личная \u0000 эффективность\n '), 'Личная эффективность');
    assert.equal(normalizeGenreName(null), '');
    assert.equal(findGenreByName(GENRES, '  ПСИХОЛОГИЯ ')?.id, 20);
    assert.equal(findGenreByName(GENRES, 'ёмкие  истории')?.id, 30);
    assert.equal(findGenreByName(GENRES, 'Роман'), null);
    assert.equal(findGenreByName(GENRES, '   '), null);
});

test('«1 книга», «3 книги», «12 книг», «21 книга»', () => {
    assert.equal(formatBookCount(1), '1 книга');
    assert.equal(formatBookCount(3), '3 книги');
    assert.equal(formatBookCount(5), '5 книг');
    assert.equal(formatBookCount(12), '12 книг');
    assert.equal(formatBookCount(21), '21 книга');
    assert.equal(formatBookCount(114), '114 книг');
    assert.equal(formatBookCount(0), '0 книг');
});
