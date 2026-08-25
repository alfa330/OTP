import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const source = readFileSync(join(root, 'src', 'components', 'wiki', 'WikiHome.jsx'), 'utf8');
const backend = readFileSync(join(root, 'wiki', 'articles.py'), 'utf8');

/**
 * Полка «Продолжить чтение» на витрине вики.
 *
 * До этого полка резалась четвёркой в обе стороны: сервер отдавал шесть строк,
 * фронт показывал четыре, и пятая по счёту прочитанная статья была недостижима
 * с витрины вовсе. Теперь глубина — десять, и они разложены по страницам.
 *
 * Ломается это молча и с двух концов сразу: вернувшийся на фронт `.slice(0, 4)`
 * оставит пейджер с одной страницей, а откат серверного потолка к шести
 * превратит обещанные «из 10» в «из 6». Поэтому сторожатся оба конца.
 */

const PER_PAGE = 4;

// Та же арифметика, что в компоненте.
function paging(total, page) {
    const pageCount = Math.max(1, Math.ceil(total / PER_PAGE));
    const safePage = Math.min(page, pageCount);
    const start = (safePage - 1) * PER_PAGE;
    const shown = Math.max(0, Math.min(PER_PAGE, total - start));
    return { pageCount, safePage, from: start + 1, to: start + shown, shown };
}

test('десять прочитанных статей достижимы: три страницы по четыре', () => {
    const { pageCount } = paging(10, 1);
    assert.equal(pageCount, 3);
    const seen = new Set();
    for (let p = 1; p <= pageCount; p += 1) {
        const { from, to } = paging(10, p);
        for (let i = from; i <= to; i += 1) {
            assert.ok(!seen.has(i), `статья ${i} показана на двух страницах`);
            seen.add(i);
        }
    }
    assert.equal(seen.size, 10, 'до части истории не добраться ни одной страницей');
    assert.equal(paging(10, 3).shown, 2, 'последняя страница — хвост, а не полная пачка');
});

test('короткая история страниц не заводит', () => {
    for (const total of [0, 1, 3, 4]) {
        assert.equal(paging(total, 1).pageCount, 1, `${total} прочитанных`);
    }
    assert.equal(paging(5, 1).pageCount, 2, 'пятая статья и есть причина страниц');
});

test('страница за концом списка схлопывается к последней', () => {
    // Сменили пространство — история стала короче, а страница осталась третьей.
    assert.equal(paging(3, 3).safePage, 1);
    assert.equal(paging(3, 3).shown, 3);
    assert.equal(paging(0, 3).safePage, 1);
    assert.equal(paging(0, 3).shown, 0);
});

test('полка рисует страницу, а не первые четыре записи', () => {
    assert.match(source, /const RECENT_PER_PAGE = 4;/, 'размер страницы объявлен явно');
    assert.match(source, /const recent = home\?\.recent \|\| \[\];/,
        'история не режется на входе — иначе листать будет нечего');
    assert.ok(source.includes('{recentShown.map('), 'карточки берутся из текущей страницы');
    assert.ok(!/recent\.map\(/.test(source), 'полка снова рисует весь список мимо страниц');
    assert.equal(source.split('<IosPager').length - 1, 1, 'ровно один пейджер на витрине');
});

test('пейджер появляется только когда есть что листать', () => {
    assert.match(source, /footer=\{recent\.length > RECENT_PER_PAGE \?/,
        'на истории из двух статей пейджер обещал бы несуществующие страницы');
});

test('сервер отдаёт историю глубже одной страницы', () => {
    const signature = backend.match(
        /def recent_and_popular\(cursor, visible_ids, user_id, limit=\d+, recent_limit=(\d+)\)/);
    assert.ok(signature, 'подпись recent_and_popular изменилась — проверь тест');
    const depth = Number(signature[1]);
    assert.ok(depth >= 10, `сервер отдаёт ${depth} прочитанных — витрина обещает десять`);
    assert.ok(depth > PER_PAGE, 'страницы без глубины бессмысленны');

    // Потолок должен уходить именно в запрос истории чтения, а не остаться
    // объявленным в подписи: перепутанный параметр не падает, он молча режет.
    const start = backend.indexOf('FROM wiki_user_reading_history h');
    assert.ok(start > 0, 'запрос истории чтения не найден — проверь тест');
    const query = backend.slice(start, start + 600);
    assert.ok(/\(user_id, ids, recent_limit\),/.test(query),
        'запрос истории чтения связан не с recent_limit');
});
