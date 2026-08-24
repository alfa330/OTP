import test from 'node:test';
import assert from 'node:assert/strict';

import {
  shiftHistoryActionLabel,
  shiftHistoryActorLabel,
  shiftHistoryCellKey,
  shiftHistoryTimeLabel,
  shiftHistoryTone,
  shiftHistoryTooltipLine,
  shiftHistoryWhenLabel,
} from '../src/components/schedule/shiftHistoryFormat.js';

/*
 * Подписи истории изменений графика (задача #235).
 *
 * Постановка требует, чтобы в истории было видно ИСТОЧНИК: смену взяли с
 * аукциона или её поставил супервайзер (и тогда — кто именно). Источник и автор
 * — две разные оси, и в подписи одновременно они не появляются: «Взята с
 * аукциона · оператор Петров» дублировало бы имя, которое и так стоит в строке
 * графика. Эти тесты и стерегут границу между осями.
 */

const NOW = new Date('2026-08-24T12:00:00');

test('источник меняет формулировку одного и того же действия', () => {
  assert.equal(
    shiftHistoryActionLabel({ action: 'added', source: 'auction' }),
    'Взята с аукциона',
  );
  assert.equal(
    shiftHistoryActionLabel({ action: 'added', source: 'supervisor' }),
    'Смена добавлена',
  );
  assert.equal(
    shiftHistoryActionLabel({ action: 'removed', source: 'status_period' }),
    'Снята статусом',
  );
  assert.equal(
    shiftHistoryActionLabel({ action: 'changed', source: 'swap' }),
    'Пересобрана обменом',
  );
});

test('незнакомое действие показывается кодом, а не прячется', () => {
  assert.equal(shiftHistoryActionLabel({ action: 'teleported', source: 'system' }), 'teleported');
  assert.equal(shiftHistoryActionLabel({}), '—');
});

test('ФИО с ролью показывается для ручных правок', () => {
  assert.equal(
    shiftHistoryActorLabel({ source: 'supervisor', actorName: 'Иванов Иван', actorRole: 'sv' }),
    'Супервайзер Иванов Иван',
  );
  assert.equal(
    shiftHistoryActorLabel({ source: 'import', actorName: 'Иванов Иван', actorRole: 'admin' }),
    'Администратор Иванов Иван',
  );
});

test('у аукционных источников автор не дублируется', () => {
  for (const source of ['auction', 'auction_topup', 'auction_topup_cancel']) {
    assert.equal(
      shiftHistoryActorLabel({ source, actorName: 'Петров Пётр', actorRole: 'operator' }),
      '',
      `источник ${source} должен говорить сам за себя`,
    );
  }
  // А вот выдача администратором — это уже поступок человека, его показываем.
  assert.equal(
    shiftHistoryActorLabel({ source: 'auction_admin', actorName: 'Петров Пётр', actorRole: 'admin' }),
    'Администратор Петров Пётр',
  );
});

test('неизвестная роль не ломает подпись', () => {
  assert.equal(
    shiftHistoryActorLabel({ source: 'supervisor', actorName: 'Иванов Иван', actorRole: 'wizard' }),
    'Иванов Иван',
  );
  assert.equal(shiftHistoryActorLabel({ source: 'supervisor', actorName: '' }), '');
});

test('времена: добавление, удаление и перенос читаются по-разному', () => {
  assert.equal(
    shiftHistoryTimeLabel({ start: '09:00', end: '17:00' }),
    '09:00 — 17:00',
  );
  assert.equal(
    shiftHistoryTimeLabel({ prevStart: '09:00', prevEnd: '17:00' }),
    '09:00 — 17:00',
  );
  assert.equal(
    shiftHistoryTimeLabel({ start: '10:00', end: '18:00', prevStart: '09:00', prevEnd: '17:00' }),
    '09:00 — 17:00 → 10:00 — 18:00',
  );
  assert.equal(shiftHistoryTimeLabel({}), '');
});

test('выходной не показывает времён', () => {
  assert.equal(shiftHistoryTimeLabel({ action: 'day_off_set' }), '');
});

test('цвет несёт смысл действия, нейтральное остаётся серым', () => {
  assert.equal(shiftHistoryTone({ action: 'added' }), 'green');
  assert.equal(shiftHistoryTone({ action: 'removed' }), 'red');
  assert.equal(shiftHistoryTone({ action: 'changed' }), 'blue');
  assert.equal(shiftHistoryTone({ action: 'day_off_cleared' }), 'slate');
  assert.equal(shiftHistoryTone({ action: 'teleported' }), 'slate');
});

test('дата: год пишем только когда он не текущий', () => {
  assert.equal(shiftHistoryWhenLabel('2026-08-24T10:53:00', NOW), '24.08, 10:53');
  assert.equal(shiftHistoryWhenLabel('2025-12-31T23:05:00', NOW), '31.12.2025, 23:05');
});

test('пустая и битая дата не превращаются в 1970 год', () => {
  assert.equal(shiftHistoryWhenLabel(null, NOW), '');
  assert.equal(shiftHistoryWhenLabel('', NOW), '');
  assert.equal(shiftHistoryWhenLabel('не дата', NOW), '');
});

test('подсказка в сетке собирает действие, автора и время', () => {
  assert.equal(
    shiftHistoryTooltipLine({
      count: 1,
      lastAt: '2026-08-24T10:53:00',
      lastAction: 'changed',
      lastSource: 'supervisor',
      lastActorName: 'Иванов Иван',
      lastActorRole: 'sv',
    }, NOW),
    'Смена изменена · Супервайзер Иванов Иван · 24.08, 10:53',
  );
});

test('в подсказке видно, что правок было несколько', () => {
  const line = shiftHistoryTooltipLine({
    count: 3,
    lastAt: '2026-08-24T10:53:00',
    lastAction: 'added',
    lastSource: 'auction',
  }, NOW);
  assert.equal(line, 'Взята с аукциона · 24.08, 10:53 · всего правок: 3');
});

test('пустая сводка не даёт подсказки', () => {
  assert.equal(shiftHistoryTooltipLine(null, NOW), '');
});

test('ключ ячейки совпадает с форматом выделения в сетке', () => {
  assert.equal(shiftHistoryCellKey(742, '2026-08-14'), '742|2026-08-14');
});
