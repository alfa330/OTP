import test from 'node:test';
import assert from 'node:assert/strict';

import {
  freedByMove,
  momentPassed,
  moveTimeline,
  occupiedAroundDay,
  overlapsAny,
  requestIntervalText,
  shiftDayKey,
} from '../src/components/schedule/shiftChangeForm.js';

/*
 * Форма «Заявка по смене» с новыми видами — «Изменить смену» и «Выходной»
 * (задача #349).
 *
 * Сервер проверяет всё сам; форма повторяет его проверки, чтобы человек узнал
 * об отказе до отправки. Расхождение не роняет заявку, но врёт: полоса
 * показывает свободное время там, где сервер ответит «уже есть смена», или
 * наоборот — гасит кнопку у заявки, которую сервер принял бы. Эти тесты
 * держат арифметику формы рядом с серверной (tests/test_shift_change_requests.py,
 * MoveValidationTests) — на тех же примерах.
 */

const minutes = (start, end) => {
  const toMin = (value) => {
    const [h, m] = String(value).split(':').map(Number);
    return h * 60 + m;
  };
  if (!start || !end) return null;
  const startMin = toMin(start);
  let endMin = toMin(end);
  if (endMin <= startMin) endMin += 1440;
  return { startMin, endMin };
};
const schedule = (map) => (dayKey) => map[dayKey] || [];
const seg = (start, end) => ({ start, end });

test('сдвиг дня считается по календарю, а не по часам', () => {
  assert.equal(shiftDayKey('2026-10-14', 0), '2026-10-14');
  assert.equal(shiftDayKey('2026-10-14', 1), '2026-10-15');
  assert.equal(shiftDayKey('2026-10-01', -1), '2026-09-30');
  assert.equal(shiftDayKey('2026-12-31', 1), '2027-01-01');
  assert.equal(shiftDayKey('2028-02-28', 1), '2028-02-29');
  assert.equal(shiftDayKey('', 1), '');
});

test('момент «уже наступил»: ровно в начало смены — уже нельзя', () => {
  // сейчас 14 октября, 09:00
  assert.equal(momentPassed('2026-10-14', 540, '2026-10-14', 540), true);
  assert.equal(momentPassed('2026-10-14', 541, '2026-10-14', 540), false);
  assert.equal(momentPassed('2026-10-13', 1380, '2026-10-14', 540), true, 'вчерашний вечер прошёл');
  assert.equal(momentPassed('2026-10-15', 0, '2026-10-14', 1439), false, 'завтра ещё не наступило');
  assert.equal(momentPassed('', 540, '2026-10-14', 540), false);
  assert.equal(momentPassed('2026-10-14', Number.NaN, '2026-10-14', 540), false);
});

test('соприкасающиеся смены не пересекаются', () => {
  const busy = [{ startMin: 540, endMin: 780 }];
  assert.equal(overlapsAny(busy, 780, 1020), false, '13:00-17:00 встык после 09:00-13:00');
  assert.equal(overlapsAny(busy, 300, 540), false, '05:00-09:00 встык перед');
  assert.equal(overlapsAny(busy, 779, 1020), true);
  assert.equal(overlapsAny([], 0, 1440), false);
  assert.equal(overlapsAny(null, 0, 1440), false);
});

test('занятое вокруг нового дня: свой кусок не в счёт', () => {
  const shifts = schedule({ '2026-10-14': [seg('09:00', '18:00')] });
  const moved = { date: '2026-10-14', startMin: 540, endMin: 1080 };
  assert.deepEqual(occupiedAroundDay('2026-10-14', shifts, minutes, moved), []);
  // Без указания переносимого куска он честно занят.
  assert.deepEqual(occupiedAroundDay('2026-10-14', shifts, minutes), [{ startMin: 540, endMin: 1080 }]);
});

test('занятое вокруг нового дня: другой кусок того же дня остаётся', () => {
  const shifts = schedule({ '2026-10-14': [seg('09:00', '13:00'), seg('15:00', '19:00')] });
  const moved = { date: '2026-10-14', startMin: 900, endMin: 1140 };
  const busy = occupiedAroundDay('2026-10-14', shifts, minutes, moved);
  assert.deepEqual(busy, [{ startMin: 540, endMin: 780 }]);
  assert.equal(overlapsAny(busy, 720, 960), true, '12:00-16:00 задевает утренний кусок');
  assert.equal(overlapsAny(busy, 780, 1020), false, '13:00-17:00 встык — можно');
});

test('занятое вокруг нового дня: хвост вчерашней ночной и утро завтрашнего', () => {
  const shifts = schedule({
    '2026-10-15': [seg('20:00', '08:00')],
    '2026-10-17': [seg('01:00', '09:00')],
  });
  const busy = occupiedAroundDay('2026-10-16', shifts, minutes);
  assert.deepEqual(busy, [
    { startMin: -240, endMin: 480 },    // ночная 15-го: до 08:00 шестнадцатого
    { startMin: 1500, endMin: 1980 },   // 01:00-09:00 семнадцатого
  ]);
  assert.equal(overlapsAny(busy, 360, 720), true, '06:00-12:00 попадает на хвост ночной');
  assert.equal(overlapsAny(busy, 480, 720), false, '08:00-12:00 — сразу после хвоста');
  assert.equal(overlapsAny(busy, 1200, 1560), true, '20:00-02:00 задевает завтрашнее утро');
  assert.equal(overlapsAny(busy, 1200, 1500), false, '20:00-01:00 — встык');
});

test('ночная смена переезжает на свой же хвост без конфликта', () => {
  const shifts = schedule({ '2026-10-14': [seg('17:00', '02:00')] });
  const moved = { date: '2026-10-14', startMin: 1020, endMin: 1560 };
  assert.deepEqual(occupiedAroundDay('2026-10-15', shifts, minutes, moved), []);
});

test('смены без границ и пустой новый день занятого не дают', () => {
  const shifts = schedule({ '2026-10-14': [{ start: '', end: '' }, null, seg('09:00', '18:00')] });
  assert.deepEqual(occupiedAroundDay('2026-10-14', shifts, minutes), [{ startMin: 540, endMin: 1080 }]);
  assert.deepEqual(occupiedAroundDay('', shifts, minutes), []);
});

test('что освобождается от старого места смены', () => {
  const old = { startMin: 540, endMin: 1080 };   // 09:00-18:00
  // сдвиг позже: освободилось утро
  assert.deepEqual(freedByMove(old, { startMin: 660, endMin: 1200 }), [{ start: 540, end: 660 }]);
  // сдвиг раньше: освободился вечер
  assert.deepEqual(freedByMove(old, { startMin: 420, endMin: 960 }), [{ start: 960, end: 1080 }]);
  // новое место в середине старого — два куска
  assert.deepEqual(freedByMove(old, { startMin: 660, endMin: 900 }), [
    { start: 540, end: 660 },
    { start: 900, end: 1080 },
  ]);
  // новое место накрывает старое — ничего не освободилось
  assert.deepEqual(freedByMove(old, { startMin: 480, endMin: 1140 }), []);
  // не пересеклись вовсе — освободилось всё старое место, а не «ничего»
  assert.deepEqual(freedByMove(old, { startMin: 1140, endMin: 1320 }), [{ start: 540, end: 1080 }]);
  assert.deepEqual(freedByMove(old, { startMin: 300, endMin: 480 }), [{ start: 540, end: 1080 }]);
  // встык — тоже всё
  assert.deepEqual(freedByMove(old, { startMin: 1080, endMin: 1200 }), [{ start: 540, end: 1080 }]);
  assert.deepEqual(freedByMove(null, { startMin: 0, endMin: 60 }), []);
  assert.deepEqual(freedByMove(old, null), []);
});

test('полосы переноса внутри дня: остальные смены серым, освободившееся розовым', () => {
  const timeline = moveTimeline({
    seg: { startMin: 900, endMin: 1140 },            // 15:00-19:00
    proposed: { startMin: 780, endMin: 1020 },       // 13:00-17:00
    occupied: [{ startMin: 540, endMin: 780 }],      // 09:00-13:00
    movesDay: false,
    conflict: false,
  });
  assert.deepEqual(timeline.base, [{ start: 540, end: 780 }]);
  assert.deepEqual(timeline.cut, [{ start: 1020, end: 1140 }]);
  assert.deepEqual(timeline.add, [{ start: 780, end: 1020 }]);
  assert.equal(timeline.addTone, 'blue');
  assert.equal(timeline.conflict, false);
});

test('полосы переноса в другой день: старого места на оси нет', () => {
  const timeline = moveTimeline({
    seg: { startMin: 900, endMin: 1140 },
    proposed: { startMin: 900, endMin: 1140 },
    occupied: [{ startMin: 600, endMin: 840 }],
    movesDay: true,
    conflict: true,
  });
  assert.deepEqual(timeline.cut, []);
  assert.deepEqual(timeline.base, [{ start: 600, end: 840 }]);
  assert.equal(timeline.conflict, true);
});

test('ось переноса: хвост вчерашней ночи с нуля, завтрашнее утро — только до конца оси', () => {
  const occupied = [
    { startMin: -240, endMin: 480 },     // вчерашняя ночная
    { startMin: 1500, endMin: 1980 },    // завтра 01:00-09:00
  ];
  // Новое место кончается в полночь: завтрашнего утра на оси нет вовсе.
  const day = moveTimeline({ seg: { startMin: 600, endMin: 840 }, proposed: { startMin: 1200, endMin: 1440 }, occupied, movesDay: true });
  assert.deepEqual(day.base, [{ start: 0, end: 480 }]);
  // Новое место уходит за полночь: ось тянется до его конца, утро видно в этой части.
  const night = moveTimeline({ seg: { startMin: 600, endMin: 840 }, proposed: { startMin: 1200, endMin: 1560 }, occupied, movesDay: true });
  assert.deepEqual(night.base, [{ start: 0, end: 480 }, { start: 1500, end: 1560 }]);
});

test('ось переноса растёт под собственную ночную смену нового дня', () => {
  const timeline = moveTimeline({
    seg: { startMin: 540, endMin: 780 },
    proposed: { startMin: 540, endMin: 780 },
    occupied: [{ startMin: 1200, endMin: 1920 }],    // 20:00-08:00 нового дня
    movesDay: true,
  });
  assert.deepEqual(timeline.base, [{ start: 1200, end: 1920 }]);
});

const formatDay = (value) => `<${value}>`;

test('подпись заявки: прежние виды читаются как раньше', () => {
  assert.equal(
    requestIntervalText({ kind: 'extra', payload: { start: '19:00', end: '22:00' } }, formatDay),
    '19:00 — 22:00',
  );
  assert.equal(
    requestIntervalText({
      kind: 'shorten',
      shiftDate: '2026-10-14',
      payload: { segment: { start: '09:00', end: '18:00' }, newStart: '09:00', newEnd: '15:00' },
    }, formatDay),
    '09:00 — 18:00 → 09:00 — 15:00',
  );
});

test('подпись переноса: новая дата названа только когда день другой', () => {
  const base = { segment: { start: '15:00', end: '19:00' }, newStart: '15:00', newEnd: '19:00' };
  assert.equal(
    requestIntervalText({ kind: 'change', shiftDate: '2026-10-14', payload: { ...base, newDate: '2026-10-17' } }, formatDay),
    '15:00 — 19:00 → <2026-10-17> · 15:00 — 19:00',
  );
  assert.equal(
    requestIntervalText({ kind: 'change', shiftDate: '2026-10-14', payload: { ...base, newDate: '2026-10-14', newStart: '13:00', newEnd: '17:00' } }, formatDay),
    '15:00 — 19:00 → 13:00 — 17:00',
  );
  // Заявка без newDate (перенос внутри дня) дату не выдумывает.
  assert.equal(
    requestIntervalText({ kind: 'change', shiftDate: '2026-10-14', payload: { ...base, newStart: '13:00', newEnd: '17:00' } }, formatDay),
    '15:00 — 19:00 → 13:00 — 17:00',
  );
  // У сокращения дата не появляется, даже если поле почему-то есть.
  assert.equal(
    requestIntervalText({ kind: 'shorten', shiftDate: '2026-10-14', payload: { ...base, newDate: '2026-10-17' } }, formatDay),
    '15:00 — 19:00 → 15:00 — 19:00',
  );
});

test('подпись выходного: все снимаемые смены, а не прочерки', () => {
  assert.equal(
    requestIntervalText({
      kind: 'day_off',
      shiftDate: '2026-10-14',
      payload: { shifts: [{ start: '09:00', end: '13:00' }, { start: '15:00', end: '19:00' }], totalMin: 480 },
    }, formatDay),
    '09:00 — 13:00, 15:00 — 19:00',
  );
  assert.equal(requestIntervalText({ kind: 'day_off', payload: {} }, formatDay), '—');
  assert.equal(requestIntervalText({ kind: 'day_off' }, formatDay), '—');
});

test('подпись не падает на пустой и чужой заявке', () => {
  assert.equal(requestIntervalText(null, formatDay), '— — — → — — —');
  assert.equal(requestIntervalText({ kind: 'extra', payload: null }, formatDay), '— — —');
  assert.equal(requestIntervalText({ kind: 'change', payload: { segment: null } }), '— — — → — — —');
});
