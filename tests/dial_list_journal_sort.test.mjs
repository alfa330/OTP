/*
 * «Удаленный КЦ» → «Журнал» → сортировка (владелец, 10.11.2026): «Сначала свежие» — не по-деловому;
 * стало «Сначала новые», добавилось «Сначала старые».
 *
 * Что защищаем:
 *   • названия и порядок пунктов: новые, старые, затем остальные — как были;
 *   • каждый пункт есть в JOURNAL_SORTS сервера: незнакомое значение сервер молча заменяет
 *     на «Сначала новые», и «Сначала старые» тихо показывали бы то же самое.
 * Порядок строк на настоящем SQL — tests/test_dial_list_journal_sort_postgres.py.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8').split('\r\n').join('\n');

const journal = read('../src/components/dial_list/DialListJournal.jsx');
const block = journal.slice(journal.indexOf('const SORT_OPTIONS'), journal.indexOf('];', journal.indexOf('const SORT_OPTIONS')));
const options = [...block.matchAll(/\{ value: '([a-z_]+)', label: '([^']+)' \}/g)].map((m) => [m[1], m[2]]);

test('сортировка журнала: «Сначала новые», «Сначала старые», остальные — как были', () => {
    assert.deepEqual(options, [
        ['activity', 'Сначала новые'],
        ['activity_asc', 'Сначала старые'],
        ['name', 'По ФИО'],
        ['created', 'По дате загрузки'],
        ['attempts', 'Больше попыток'],
    ]);
    assert.doesNotMatch(block, /свеж/i);
});

test('каждый пункт сортировки сервер знает', () => {
    const service = read('../dial_list/service.py');
    const sorts = service.slice(service.indexOf('JOURNAL_SORTS = {'), service.indexOf('}', service.indexOf('JOURNAL_SORTS = {')));
    const keys = [...sorts.matchAll(/^\s+"([a-z_]+)":/gm)].map((m) => m[1]);
    assert.deepEqual([...keys].sort(), options.map(([value]) => value).sort());
    assert.match(journal, /useState\('activity'\)/);    // по умолчанию — «Сначала новые», как раньше «свежие»
});
