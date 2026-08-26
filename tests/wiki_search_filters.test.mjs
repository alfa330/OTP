import assert from 'node:assert/strict';
import test from 'node:test';

import {
    EMPTY_FILTERS, MATCH_ALL, MATCH_TITLE, activeCount, filterChips, filtersKey,
    isDefaultFilters, normalizeFilters, searchParams, toggleValue, withoutChip,
} from '../src/components/wiki/searchFilters.js';

const qs = (params) => params.toString();

test('пустые фильтры не добавляют в адрес ничего лишнего', () => {
    assert.equal(qs(searchParams('аренда', EMPTY_FILTERS)), 'q=%D0%B0%D1%80%D0%B5%D0%BD%D0%B4%D0%B0');
    assert.ok(isDefaultFilters(EMPTY_FILTERS));
    assert.equal(activeCount(EMPTY_FILTERS), 0);
});

test('несколько типов уезжают ПОВТОРОМ ключа, а не как article_type[]', () => {
    // Сервер читает их через request.args.getlist('article_type'): форма с
    // квадратными скобками (то, как axios сериализует массив по умолчанию)
    // не дала бы ему ни одного значения, и фильтр молча перестал бы работать.
    const params = searchParams('x', { types: ['regulation', 'instruction'] });
    assert.deepEqual(params.getAll('article_type'), ['regulation', 'instruction']);
    assert.ok(!qs(params).includes('%5B%5D'));
});

test('создатели уезжают числами, каждый своим ключом', () => {
    const params = searchParams('x', { authors: [11, 22] });
    assert.deepEqual(params.getAll('author_id'), ['11', '22']);
});

test('область поиска пишется в match и только когда она не «везде»', () => {
    assert.equal(searchParams('x', { match: MATCH_ALL }).get('match'), null);
    assert.equal(searchParams('x', { match: MATCH_TITLE }).get('match'), 'title');
});

test('пустые значения из extra в адрес не попадают', () => {
    // space_id === null — обычное состояние «пространство ещё не известно»:
    // отправь мы его как есть, сервер прочитал бы строку «null».
    const params = searchParams('x', EMPTY_FILTERS, { space_id: null, limit: 5 });
    assert.equal(params.get('space_id'), null);
    assert.equal(params.get('limit'), '5');
});

test('запрос обрезается по краям', () => {
    assert.equal(searchParams('  аренда  ', EMPTY_FILTERS).get('q'), 'аренда');
});

test('область считается одним фильтром, а не по числу вариантов', () => {
    assert.equal(activeCount({ types: ['regulation'], match: MATCH_TITLE }), 2);
    assert.equal(activeCount({ types: ['regulation'], authors: [1, 2] }), 3);
});

test('переключение значения не переставляет остальные', () => {
    assert.deepEqual(toggleValue(['a', 'b'], 'c'), ['a', 'b', 'c']);
    assert.deepEqual(toggleValue(['a', 'b', 'c'], 'b'), ['a', 'c']);
    assert.deepEqual(toggleValue(undefined, 'a'), ['a']);
});

test('битые фильтры не роняют сборку адреса', () => {
    const value = normalizeFilters({ types: 'регламент', authors: null, match: 'выдумка' });
    assert.deepEqual(value, { types: [], authors: [], match: MATCH_ALL });
    assert.equal(qs(searchParams('x', null)), 'q=x');
});

test('чипы подписаны, а неизвестный автор не оставляет пустого места', () => {
    const chips = filterChips(
        { types: ['regulation'], authors: [11, 99], match: MATCH_TITLE },
        [{ id: 11, name: 'Айгуль' }],
    );
    assert.deepEqual(chips.map((c) => c.label),
        ['Только в названиях', 'Регламенты', 'Айгуль', 'Создатель']);
});

test('снятие чипа убирает ровно его', () => {
    const filters = { types: ['regulation', 'instruction'], authors: [11], match: MATCH_TITLE };
    const chips = filterChips(filters, []);
    assert.deepEqual(withoutChip(filters, chips[1]).types, ['instruction']);
    assert.deepEqual(withoutChip(filters, chips[3]).authors, []);
    assert.equal(withoutChip(filters, chips[0]).match, MATCH_ALL);
});

test('ключ фильтров меняется только вместе с выбором', () => {
    // Объект фильтров пересобирается на каждый рендер родителя: поставь его в
    // зависимости эффекта как есть — поиск уходил бы на сервер по кругу.
    assert.equal(filtersKey({ types: ['a'], authors: [1], match: MATCH_TITLE }),
                 filtersKey({ types: ['a'], authors: [1], match: MATCH_TITLE }));
    assert.notEqual(filtersKey({ types: ['a'] }), filtersKey({ types: ['b'] }));
});
