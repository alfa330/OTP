// «Списки Байги» (#356): фильтры, адрес, вставленный список, подписи и числа.
// Пары с сервером (размеры страниц, режимы приза, ключи сортировки и
// диапазонов) сверяет tests/test_baiga.py.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
    ALL_SHEETS, DEFAULT_PAGE_SIZE, EMPTY_FILTERS, PRIZE_ANY, applyStateToUrl, fileNameFromDisposition, filterChips,
    filtersPayload, fmtStamp, focusFilters, formatInt, formatMoney, groupRows, isAllSheets, isGrouped, isSearching,
    nextSort,
    panelFilterCount, parseWhole, periodLabel, rangeErrors, readStateFromSearch, shortId, splitTokens,
    stripBaigaParams, weekLabel, weekShort,
} from '../src/components/baiga/baigaMeta.js';

const ID = 'e549ba7d00000000000000000000a7e2';

test('числа — с узким пробелом, деньги — со знаком тенге', () => {
    assert.equal(formatInt(2767000), '2 767 000');
    assert.equal(formatInt(950), '950');
    assert.equal(formatMoney(200000), '200 000 ₸');
    assert.equal(formatInt('не число'), '');
});

test('период и неделя читаются по-русски', () => {
    assert.equal(periodLabel('2026-09-21', '2026-09-27'), '21–27 сентября');
    assert.equal(periodLabel('2026-09-28', '2026-10-04'), '28 сентября – 4 октября');
    assert.equal(weekShort('2026-09-21', '2026-09-27'), '21–27.09');
    assert.equal(weekShort('2026-09-28', '2026-10-04'), '28.09–04.10');
    assert.equal(weekShort('2026-09-21'), '21.09');
    assert.equal(weekLabel({ period_start: '2026-09-21', period_end: '2026-09-27', week_number: 39 }),
        'Неделя 39 · 21–27 сентября');
    assert.equal(fmtStamp('2026-10-02T12:05:00'), '02.10.2026, 12:05');
    assert.equal(shortId(ID), 'e549ba7d…');
});

test('диапазоны: только целые, пустое — не задано', () => {
    assert.equal(parseWhole(' 500 000 '), 500000);
    assert.equal(parseWhole(''), '');
    assert.ok(Number.isNaN(parseWhole('12.5')));
    assert.ok(Number.isNaN(parseWhole('-3')));
    const errors = rangeErrors({ ...EMPTY_FILTERS, amount_min: 'много', trips_max: '10' });
    assert.deepEqual(Object.keys(errors), ['amount_min']);
});

test('в запрос уходят только заданные фильтры, числа — числами', () => {
    const payload = filtersPayload({
        ...EMPTY_FILTERS, q: '  Жусип ', city: 'Шымкент', amount_min: '100 000', amount_max: 'x', list: 'a\nb',
    });
    assert.deepEqual(payload, { q: 'Жусип', city: 'Шымкент', list: 'a\nb', amount_min: 100000 });
});

test('чипы называют выбранное и снимаются по одному', () => {
    const filters = { ...EMPTY_FILTERS, city: 'Шымкент', prize: PRIZE_ANY, position_max: '10', amount_min: '300000' };
    const chips = filterChips(filters);
    assert.deepEqual(chips.map((chip) => [chip.name, chip.label]),
        [['Город', 'Шымкент'], ['Приз', 'Только с призом'], ['Позиция', 'до 10'], ['Сумма', 'от 300 000']]);
    assert.deepEqual(chips[2].clear, { position_min: '', position_max: '' });
    assert.equal(panelFilterCount(filters), 4);
    assert.equal(panelFilterCount({ ...EMPTY_FILTERS, q: 'x', period: '2026-09-21' }), 0);
});

test('сортировка: второе нажатие переворачивает, «Сумма» — сразу от большего', () => {
    assert.deepEqual(nextSort({ sort: 'week', dir: 'desc' }, 'amount'), { sort: 'amount', dir: 'desc' });
    assert.deepEqual(nextSort({ sort: 'amount', dir: 'desc' }, 'amount'), { sort: 'amount', dir: 'asc' });
    assert.deepEqual(nextSort({ sort: 'amount', dir: 'desc' }, 'driver'), { sort: 'driver', dir: 'asc' });
});

test('вставленный список делится так же, как на сервере', () => {
    const text = `${ID}, ZZ000002\nAB 123456\n\nZZ000003 ZZ000004;${ID.toUpperCase()}`;
    assert.deepEqual(splitTokens(text), [ID, 'ZZ000002', 'AB 123456', 'ZZ000003', 'ZZ000004', ID.toUpperCase()]);
    assert.deepEqual(splitTokens('a\na\n'), ['a']);
    assert.deepEqual(splitTokens(''), []);
});

test('фильтры живут в адресе и переживают перезагрузку', () => {
    const url = new URL('https://alfa330.github.io/OTP?view=baiga&bg_city=old&task_id=5');
    const filters = {
        ...EMPTY_FILTERS, city: 'Шымкент', zachet: 'Астана', amount_min: '100',
    };
    applyStateToUrl(url, { filters, sort: { sort: 'amount', dir: 'desc' }, size: 100 });
    assert.equal(url.searchParams.get('view'), 'baiga');
    assert.equal(url.searchParams.get('task_id'), '5');
    assert.equal(url.searchParams.get('bg_city'), 'Шымкент');
    assert.equal(url.searchParams.get('bg_zachet'), 'Астана');
    const state = readStateFromSearch(url.search);
    assert.equal(state.filters.zachet, 'Астана');
    assert.equal(state.filters.amount_min, '100');
    assert.deepEqual(state.sort, { sort: 'amount', dir: 'desc' });
    assert.equal(state.size, 100);
});

test('поиск и вставленный список в адрес не попадают: это персональные данные', () => {
    const url = new URL('https://example.com/OTP?view=baiga');
    const ids = Array.from({ length: 300 }, (_, i) => `e549ba7d000000000000000000${String(i).padStart(6, '0')}`);
    applyStateToUrl(url, { filters: { ...EMPTY_FILTERS, q: 'Жусип', list: ids.join('\n') }, sort: null, size: 50 });
    assert.equal(url.search, '?view=baiga');
    const state = readStateFromSearch('?bg_q=Жусип&bg_list=a,b');
    assert.equal(state.filters.q, '');
    assert.equal(state.filters.list, '');
});

test('листы как в Excel: «Все листы» — своё значение, на сервер уходит как «без листа»', () => {
    assert.equal(isAllSheets(''), true);
    assert.equal(isAllSheets(ALL_SHEETS), true);
    assert.equal(isAllSheets('Астана'), false);
    assert.deepEqual(filtersPayload({ ...EMPTY_FILTERS, zachet: ALL_SHEETS }), {});
    assert.deepEqual(filtersPayload({ ...EMPTY_FILTERS, zachet: 'Астана' }), { zachet: 'Астана' });
    const url = new URL('https://example.com/OTP?view=baiga');
    applyStateToUrl(url, { filters: { ...EMPTY_FILTERS, zachet: ALL_SHEETS }, sort: null, size: 50 });
    assert.equal(url.searchParams.get('bg_zachet'), ALL_SHEETS);
});

test('поиск — по всей книге: признак поиска', () => {
    assert.equal(isSearching({ ...EMPTY_FILTERS }), false);
    assert.equal(isSearching({ ...EMPTY_FILTERS, q: '  ' }), false);
    assert.equal(isSearching({ ...EMPTY_FILTERS, q: 'Жусип' }), true);
    assert.equal(isSearching({ ...EMPTY_FILTERS, list: 'ZZ000001' }), true);
});

test('«Все листы» в порядке файла разделены заголовками листов', () => {
    const rows = [
        { id: 1, zachet: 'Алматы', period_start: '2026-09-21' },
        { id: 2, zachet: 'Алматы', period_start: '2026-09-21' },
        { id: 3, zachet: 'Астана', period_start: '2026-09-21' },
        { id: 4, zachet: 'Астана', period_start: '2026-09-14' },
    ];
    assert.equal(isGrouped({ zachet: ALL_SHEETS }, { sort: 'week' }), true);
    assert.equal(isGrouped({ zachet: 'Астана' }, { sort: 'week' }), false);
    assert.equal(isGrouped({ zachet: ALL_SHEETS }, { sort: 'amount' }), false);
    const one = groupRows(rows, false).map((item) => (item.type === 'group' ? `#${item.zachet}` : item.row.id));
    assert.deepEqual(one, ['#Алматы', 1, 2, '#Астана', 3, 4]);
    const byWeek = groupRows(rows, true).map((item) => (item.type === 'group' ? `#${item.zachet}` : item.row.id));
    assert.deepEqual(byWeek, ['#Алматы', 1, 2, '#Астана', 3, '#Астана', 4]);
});

test('порядок и размер по умолчанию в адрес не пишутся; мусор из адреса не доходит', () => {
    const url = new URL('https://example.com/OTP?view=baiga');
    applyStateToUrl(url, { filters: { ...EMPTY_FILTERS }, sort: { sort: 'week', dir: 'desc' }, size: DEFAULT_PAGE_SIZE });
    assert.equal(url.search, '?view=baiga');
    const state = readStateFromSearch('?bg_sort=r.id%3BDROP.asc&bg_size=7');
    assert.deepEqual(state.sort, { sort: 'week', dir: 'desc' });
    assert.equal(state.size, DEFAULT_PAGE_SIZE);
});

test('уход из раздела снимает все метки раздела и только их', () => {
    const url = new URL('https://example.com/OTP?view=tasks&bg_q=x&bg_city=y&task_id=3');
    assert.equal(stripBaigaParams(url), true);
    assert.equal(url.search, '?view=tasks&task_id=3');
    assert.equal(stripBaigaParams(url), false);
    assert.equal(stripBaigaParams(null), false);
});

test('источник помощника открывает неделю и водителя, прочие фильтры сняты', () => {
    const weeks = [{ id: 3, period_start: '2026-09-28' }, { id: 2, period_start: '2026-09-21' }];
    const focus = { weekId: 2, query: ' ZZ123456 ', nonce: 1 };
    assert.deepEqual(focusFilters(focus, weeks, 'Астана'),
        { ...EMPTY_FILTERS, zachet: 'Астана', period: '2026-09-21', q: 'ZZ123456' });
    // Поиск идёт по всей книге — и в адрес не пишется: это номер ВУ.
    assert.equal(isSearching(focusFilters(focus, weeks, 'Астана')), true);
    const url = new URL('https://example.com/OTP?view=baiga');
    applyStateToUrl(url, { filters: focusFilters(focus, weeks, ''), sort: { sort: 'week', dir: 'desc' },
        size: DEFAULT_PAGE_SIZE });
    assert.equal(url.search, '?view=baiga&bg_period=2026-09-21');
    // Неделю заменили или удалили (id больше нет) — ищем по всем неделям.
    assert.equal(focusFilters({ weekId: 99, query: 'ZZ123456' }, weeks, '').period, '');
    assert.equal(focusFilters({ weekId: null, query: 'ZZ123456' }, weeks, '').period, '');
    // Фрагмент «Недели» водителя не несёт — открывается сама неделя.
    assert.deepEqual(focusFilters({ weekId: 3, query: '' }, weeks, ALL_SHEETS),
        { ...EMPTY_FILTERS, zachet: ALL_SHEETS, period: '2026-09-28' });
    assert.deepEqual(focusFilters(null, null, undefined), { ...EMPTY_FILTERS });
});

test('имя файла берётся из ответа сервера', () => {
    const header = "attachment; filename=export.xlsx; filename*=UTF-8''%D0%A1%D0%BF%D0%B8%D1%81%D0%BE%D0%BA%20%D0%B1%D0%B0%D0%B9%D0%B3%D0%B8.xlsx";
    assert.equal(fileNameFromDisposition(header, 'x.xlsx'), 'Список байги.xlsx');
    assert.equal(fileNameFromDisposition('attachment; filename="a.xlsx"', 'x.xlsx'), 'a.xlsx');
    assert.equal(fileNameFromDisposition('', 'x.xlsx'), 'x.xlsx');
});
