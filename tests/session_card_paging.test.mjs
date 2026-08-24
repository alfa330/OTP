import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, '..', 'src', 'components', 'sessions', 'SessionUserModal.jsx'), 'utf8');

/**
 * Пагинация сессий в карточке сотрудника.
 *
 * У одного человека живых сессий бывает под сотню (на бою встречалась 81), и
 * списком они превращают карточку в бесконечную ленту: до подвала с «Прервать
 * все» не дойти. Компонент завязан на React и DOM, поэтому здесь сторожатся
 * решения, которые ломаются молча при правке разметки.
 */

const PAGE_SIZE = 10;

// Та же арифметика, что в компоненте: страницы, окно номеров, защита от
// «уехавшей» страницы после прерывания последней сессии.
function paging(total, page) {
    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
    const safePage = Math.min(page, pageCount);
    const start = (safePage - 1) * PAGE_SIZE;
    const shown = Math.max(0, Math.min(PAGE_SIZE, total - start));
    const numbers = [];
    const first = Math.max(1, Math.min(safePage - 2, pageCount - 4));
    for (let i = first; i < first + 5 && i <= pageCount; i += 1) numbers.push(i);
    return { pageCount, safePage, from: start + 1, to: start + shown, shown, numbers };
}

test('порог: до десяти сессий страниц нет', () => {
    for (const total of [0, 1, 9, 10]) {
        assert.equal(paging(total, 1).pageCount, 1, `${total} сессий`);
    }
    assert.equal(paging(11, 1).pageCount, 2);
    assert.equal(paging(81, 1).pageCount, 9, 'реальный случай с боя');
});

test('границы страниц не пересекаются и покрывают всё', () => {
    const total = 41;
    const seen = new Set();
    const { pageCount } = paging(total, 1);
    for (let p = 1; p <= pageCount; p += 1) {
        const { from, to } = paging(total, p);
        for (let i = from; i <= to; i += 1) {
            assert.ok(!seen.has(i), `строка ${i} показана дважды`);
            seen.add(i);
        }
    }
    assert.equal(seen.size, total);
    assert.deepEqual(paging(total, 1), { ...paging(total, 1), from: 1, to: 10 });
    assert.equal(paging(total, 5).to, 41, 'последняя страница — хвост, а не полная пачка');
    assert.equal(paging(total, 5).shown, 1);
});

test('страница за пределом схлопывается к последней, а не показывает пустоту', () => {
    // Прервали последнюю сессию, стоя на последней странице.
    const after = paging(30, 9);
    assert.equal(after.safePage, 3);
    assert.equal(after.shown, 10);
    assert.equal(paging(0, 7).safePage, 1);
    assert.equal(paging(0, 7).shown, 0);
});

test('окно номеров — пять штук и всегда содержит текущую', () => {
    for (const [total, page] of [[81, 1], [81, 5], [81, 9], [41, 3], [25, 2]]) {
        const { numbers, safePage, pageCount } = paging(total, page);
        assert.ok(numbers.length <= 5);
        assert.ok(numbers.includes(safePage), `страница ${safePage} выпала из окна`);
        assert.ok(numbers[0] >= 1 && numbers[numbers.length - 1] <= pageCount);
    }
    assert.deepEqual(paging(81, 1).numbers, [1, 2, 3, 4, 5]);
    assert.deepEqual(paging(81, 9).numbers, [5, 6, 7, 8, 9], 'у хвоста окно прижимается к концу');
});

test('пейджер стоит НАД списком сессий', () => {
    // Под списком до него пришлось бы прокручивать десяток карточек — ровно то,
    // от чего страницы и заводились.
    const pager = source.indexOf('<Pager');
    const cards = source.indexOf('{pageSessions.map(');
    assert.ok(pager > 0 && cards > 0, 'разметка изменилась — проверь тест');
    assert.ok(pager < cards, 'пейджер обязан идти раньше карточек сессий');
    assert.equal(source.split('<Pager').length - 1, 1, 'ровно одно место отрисовки — иначе пейджеров два');
});

test('порог страницы объявлен явно и равен десяти', () => {
    assert.match(source, /export const SESSIONS_PAGE_SIZE = 10;/);
});
