import test from 'node:test';
import assert from 'node:assert/strict';

import {
  TECHNICAL_QUERY_PARAMS,
  stripTechnicalQueryParams,
  stripTechnicalQueryParamsFromHref,
} from '../src/utils/urlHygiene.js';

// Ровно та ссылка, на которую пожаловался владелец.
const DIRTY = 'https://alfa330.github.io/OTP?view=tasks&auth_reload=1786533936834&v=1786951163258&task_id=166';

test('метки перезагрузки уходят, смысловые параметры остаются', () => {
  assert.equal(
    stripTechnicalQueryParamsFromHref(DIRTY),
    'https://alfa330.github.io/OTP?view=tasks&task_id=166'
  );
});

test('порядок оставшихся параметров не меняется', () => {
  const url = new URL('https://example.com/OTP?v=1&task_id=5&auth_reload=2&view=tasks');
  assert.equal(stripTechnicalQueryParams(url), true);
  assert.equal(url.search, '?task_id=5&view=tasks');
});

test('хэш и путь не трогаем', () => {
  assert.equal(
    stripTechnicalQueryParamsFromHref('https://example.com/OTP/lms/course/16?v=9#lesson-3'),
    'https://example.com/OTP/lms/course/16#lesson-3'
  );
});

test('чистой ссылке ничего не делаем — та же строка, без изменений', () => {
  const clean = 'https://alfa330.github.io/OTP?view=tasks&task_id=166';
  assert.equal(stripTechnicalQueryParamsFromHref(clean), clean);
  const url = new URL(clean);
  assert.equal(stripTechnicalQueryParams(url), false);
});

test('несколько значений одной метки уходят полностью', () => {
  const url = new URL('https://example.com/OTP?v=1&v=2&view=tasks');
  assert.equal(stripTechnicalQueryParams(url), true);
  assert.equal(url.searchParams.getAll('v').length, 0);
});

test('мусор на входе не роняет вызов', () => {
  assert.equal(stripTechnicalQueryParams(null), false);
  assert.equal(stripTechnicalQueryParams({}), false);
  assert.equal(stripTechnicalQueryParamsFromHref(''), '');
  assert.equal(stripTechnicalQueryParamsFromHref('не ссылка'), 'не ссылка');
});

test('список меток закрыт и известен: v и auth_reload', () => {
  assert.deepEqual(TECHNICAL_QUERY_PARAMS, ['v', 'auth_reload']);
});
