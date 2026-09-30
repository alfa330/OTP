import test from 'node:test';
import assert from 'node:assert/strict';

import {
  EXPORT_MAX_DAYS, defaultPeriod, exportFileName, exportQuery, periodProblem,
} from '../src/components/crm/exportPeriod.js';

/* Период выгрузки «Обращений» в Excel. Правила — те же, что у сервера
 * (crm/routes.py::_period): иначе «Скачать» активна, а сервер отказывает. */

test('по умолчанию — текущий месяц по сегодня', () => {
  assert.deepEqual(defaultPeriod(new Date(2026, 8, 30, 15, 0)), { from: '2026-09-01', to: '2026-09-30' });
  assert.deepEqual(defaultPeriod(new Date(2026, 0, 1, 0, 5)), { from: '2026-01-01', to: '2026-01-01' });
});

test('период проверяется как на сервере', () => {
  assert.equal(periodProblem('', '2026-09-30'), 'Укажите период: дату начала и дату окончания');
  assert.equal(periodProblem('2026-09-30', '2026-09-01'), 'Дата окончания раньше даты начала');
  assert.equal(periodProblem('2026-09-30', '2026-09-30'), null, 'один день — законный период');
  assert.equal(EXPORT_MAX_DAYS, 366);
  assert.equal(periodProblem('2025-10-01', '2026-09-30'), null, 'год целиком');
  assert.equal(periodProblem('2024-01-01', '2026-09-30'), 'Период длиннее года — выгрузите по частям');
});

test('имя файла и запрос', () => {
  assert.equal(exportFileName('2026-09-01', '2026-09-30'), 'Обращения 01.09.2026–30.09.2026.xlsx');
  assert.equal(exportQuery('2026-09-01', '2026-09-30'), 'date_from=2026-09-01&date_to=2026-09-30');
});
