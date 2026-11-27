/*
 * Поиск задач с опечатками — чистая часть фронта (src/components/tasks/taskSearch.js).
 * Владелец 27.11.2026: «поиск будет искать по выбранной доске и проекту». Сопоставляет
 * сервер (tests/test_task_search.py); здесь — когда спрашивать, что просить,
 * как подсветить и как подписать ступени выдачи.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BOARD_SEARCH_PAGE,
  SEARCH_TIER_EXACT,
  SEARCH_TIER_KEY,
  SEARCH_TIER_PARTIAL,
  SEARCH_TIER_SIMILAR,
  appendSearchPage,
  foundLabel,
  highlightSegments,
  isSearchResultList,
  isSearchableQuery,
  searchRequestParams,
  withSearchSeparators,
} from '../src/components/tasks/taskSearch.js';
import { scopeQueryParams } from '../src/components/tasks/boardQuery.js';

const join = (segments) => segments.map((segment) => (segment.mark ? `[${segment.text}]` : segment.text)).join('');

test('ищем от слова в две буквы или от номера — как сервер', () => {
  for (const value of ['ab', 'зп', 'отч', '3', 'HR-1', ' табель ', 'а 3']) {
    assert.equal(isSearchableQuery(value), true, value);
  }
  // Одни однобуквенные слова сервер не ищет, а отдаёт весь список — не спрашиваем вовсе.
  for (const value of ['', '   ', 'а', 'а б', '!!', '—', null, undefined]) {
    assert.equal(isSearchableQuery(value), false, String(value));
  }
});

test('запрос — охват доски, строка и страница, без сводки', () => {
  const scope = scopeQueryParams('my', '5', 'all');
  assert.deepEqual(searchRequestParams(scope, '  отчёт  '), {
    project_id: '5', mine: 'any', q: 'отчёт', limit: BOARD_SEARCH_PAGE, offset: 0, summary: 0,
  });
  // Доска сотрудника и раздел доски — тем же охватом, что у колонок и выгрузки.
  assert.deepEqual(searchRequestParams(scopeQueryParams('person:42', '3', '7'), 'акт', 30, 30), {
    project_id: '3', section_id: '7', person_id: 42, person_scope: 'any', q: 'акт', limit: 30, offset: 30, summary: 0,
  });
  assert.deepEqual(searchRequestParams(scopeQueryParams('all', 'all', 'all'), 'hr-12'), {
    q: 'hr-12', limit: BOARD_SEARCH_PAGE, offset: 0, summary: 0,
  });
});

test('подсветка не теряет текст и сглаживает кривые отрезки', () => {
  assert.equal(join(highlightSegments('Заполнить табель', [[10, 16]])), 'Заполнить [табель]');
  assert.equal(join(highlightSegments('Отчёт ЗП', [[6, 8], [0, 5]])), '[Отчёт] [ЗП]');
  // Перекрытые — одним куском, за краем — обрезаны, пустые и мусор — отброшены.
  assert.equal(join(highlightSegments('абвгде', [[1, 3], [2, 4], [5, 99], [3, 3], ['x', 2], null])), 'а[бвг]д[е]');
  assert.equal(join(highlightSegments('текст', [])), 'текст');
  assert.equal(join(highlightSegments('текст', undefined)), 'текст');
  assert.equal(join(highlightSegments('', [[0, 3]])), '');
  assert.equal(highlightSegments('текст', [[0, 5]]).map((segment) => segment.text).join(''), 'текст');
});

const task = (id, tier) => ({ id, subject: `Задача ${id}`, ...(tier === undefined ? {} : { search_match: { tier } }) });

test('ступени подписаны, точные и ключ — без подписи', () => {
  const rows = withSearchSeparators([
    task(1, SEARCH_TIER_KEY), task(2, SEARCH_TIER_EXACT), task(3, SEARCH_TIER_SIMILAR), task(4, SEARCH_TIER_SIMILAR),
  ]);
  assert.deepEqual(rows.map((row) => (row.type === 'separator' ? row.label : row.task.id)),
    [1, 2, 'Похожие по написанию', 3, 4]);
  // Нет точных — подпись сверху: видно, что слова запроса в задачах буквально нет.
  const similarOnly = withSearchSeparators([task(5, SEARCH_TIER_SIMILAR)]);
  assert.equal(similarOnly[0].label, 'Похожие по написанию');
  const partial = withSearchSeparators([task(6, SEARCH_TIER_PARTIAL), task(7, SEARCH_TIER_PARTIAL)]);
  assert.deepEqual(partial.map((row) => row.label || row.task.id), ['Совпали не все слова', 6, 7]);
  // Ключи строк не повторяются — React держит строки по ним.
  const keys = rows.map((row) => row.key);
  assert.equal(new Set(keys).size, keys.length);
});

test('выдача поиска узнаётся по отметке сервера', () => {
  assert.equal(isSearchResultList([task(1, 1)]), true);
  assert.equal(isSearchResultList([task(1)]), false);
  assert.equal(isSearchResultList([]), false);
  // Задача без отметки в выдаче — как точная, без подписи.
  assert.deepEqual(withSearchSeparators([task(1)]).map((row) => row.type), ['task']);
});

test('«Показать ещё» не дублирует задачи, сдвинувшиеся между запросами', () => {
  const shown = [task(1), task(2)];
  assert.deepEqual(appendSearchPage(shown, [task(2), task(3)]).map((item) => item.id), [1, 2, 3]);
  assert.deepEqual(appendSearchPage([], null).map((item) => item.id), []);
});

test('подпись над выдачей склоняется', () => {
  assert.equal(foundLabel(0), 'Ничего не нашлось');
  assert.equal(foundLabel(1), 'Нашлась 1 задача');
  assert.equal(foundLabel(2), 'Нашлось 2 задачи');
  assert.equal(foundLabel(5), 'Нашлось 5 задач');
  assert.equal(foundLabel(11), 'Нашлось 11 задач');
  assert.equal(foundLabel(21), 'Нашлась 21 задача');
  assert.equal(foundLabel(112), 'Нашлось 112 задач');
});
