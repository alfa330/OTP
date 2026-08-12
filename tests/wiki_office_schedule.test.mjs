import assert from 'node:assert/strict';
import test from 'node:test';

import {
    breakLines, officeNow, officeStatus, scheduleLines,
} from '../src/components/wiki/officeSchedule.js';

/* График офиса считается по времени офиса, а не браузера: оператор может
 * сидеть в другом городе, а «Открыто» относится к двери, которую он называет
 * водителю. Все проверки задают момент явно, в UTC, и ждут ответ по Алматы
 * (UTC+5 на всю страну с марта 2024 года).
 */

const WORKDAY = { from: '09:00', to: '19:00', break_from: '13:00', break_to: '14:00' };

// Пн–Пт 09:00–19:00, обед 13:00–14:00, суббота короткая, воскресенье выходное —
// это Костанай из справочника «Департамент».
const KOSTANAY = {
    mon: WORKDAY, tue: WORKDAY, wed: WORKDAY, thu: WORKDAY, fri: WORKDAY,
    sat: { from: '10:00', to: '13:00' },
    sun: null,
};

// 2026-08-12 — среда.
const at = (utc) => new Date(utc);

test('в рабочие часы офис открыт и знает, до скольки', () => {
    assert.deepEqual(officeStatus(KOSTANAY, at('2026-08-12T06:20:00Z')), // 11:20 Алматы
        { state: 'open', until: '19:00' });
});

test('обед показывается отдельно от «открыто»', () => {
    // 13:30 по Алматы: дверь заперта, но офис не закрыт до завтра — разница
    // важна оператору, он говорит водителю «подойдите после двух».
    assert.deepEqual(officeStatus(KOSTANAY, at('2026-08-12T08:30:00Z')),
        { state: 'break', until: '14:00' });
});

test('границы интервалов: открытие включительно, закрытие — нет', () => {
    assert.equal(officeStatus(KOSTANAY, at('2026-08-12T04:00:00Z')).state, 'open');   // 09:00
    assert.equal(officeStatus(KOSTANAY, at('2026-08-12T03:59:00Z')).state, 'closed'); // 08:59
    assert.equal(officeStatus(KOSTANAY, at('2026-08-12T14:00:00Z')).state, 'closed'); // 19:00
    assert.equal(officeStatus(KOSTANAY, at('2026-08-12T13:59:00Z')).state, 'open');   // 18:59
    assert.equal(officeStatus(KOSTANAY, at('2026-08-12T08:00:00Z')).state, 'break');  // 13:00
    assert.equal(officeStatus(KOSTANAY, at('2026-08-12T09:00:00Z')).state, 'open');   // 14:00
});

test('до открытия — сегодняшнее время без названия дня', () => {
    assert.deepEqual(officeStatus(KOSTANAY, at('2026-08-12T02:00:00Z')), // 07:00 среда
        { state: 'closed', opensAt: '09:00', opensDay: null });
});

test('после закрытия — ближайший рабочий день, а не завтрашний', () => {
    // Вечер субботы: воскресенье выходное, значит открытие в понедельник.
    assert.deepEqual(officeStatus(KOSTANAY, at('2026-08-15T15:00:00Z')), // 20:00 суббота
        { state: 'closed', opensAt: '09:00', opensDay: 'Пн' });
});

test('в выходной день офис закрыт', () => {
    assert.deepEqual(officeStatus(KOSTANAY, at('2026-08-16T07:00:00Z')), // 12:00 воскресенье
        { state: 'closed', opensAt: '09:00', opensDay: 'Пн' });
});

test('время берётся по Алматы, а не по машине пользователя', () => {
    // 22:00 UTC — по Гринвичу среда почти кончилась, в Алматы уже 03:00 четверга.
    const now = officeNow(at('2026-08-12T22:00:00Z'));
    assert.deepEqual(now, { dayIndex: 3, minutes: 180 });
    assert.equal(officeStatus(KOSTANAY, at('2026-08-12T22:00:00Z')).opensDay, null);
});

test('ночная смена через полночь остаётся открытой', () => {
    const nightly = { fri: { from: '22:00', to: '04:00' } };
    assert.equal(officeStatus(nightly, at('2026-08-14T18:00:00Z')).state, 'open');  // Пт 23:00
    assert.equal(officeStatus(nightly, at('2026-08-14T21:00:00Z')).state, 'open');  // Сб 02:00
    assert.equal(officeStatus(nightly, at('2026-08-14T23:30:00Z')).state, 'closed'); // Сб 04:30
});

test('пустой график не выдаёт себя за «закрыто»', () => {
    // «ОНЛАЙН»-офисы: часов нет вообще. Показать «Закрыто» — соврать.
    for (const empty of [null, undefined, {}, { mon: null }, { mon: { from: '09:00' } }]) {
        assert.equal(officeStatus(empty).state, 'none', JSON.stringify(empty));
    }
});

test('неделя сворачивается в диапазоны дней', () => {
    assert.deepEqual(scheduleLines(KOSTANAY), [
        { days: 'Пн–Пт', time: '09:00–19:00', isDayOff: false },
        { days: 'Сб', time: '10:00–13:00', isDayOff: false },
        { days: 'Вс', time: 'выходной', isDayOff: true },
    ]);
});

test('одинаковая неделя — одна строка', () => {
    const everyDay = Object.fromEntries(
        ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map((d) => [d, WORKDAY]));
    assert.deepEqual(scheduleLines(everyDay), [
        { days: 'Пн–Вс', time: '09:00–19:00', isDayOff: false },
    ]);
});

test('обед одинаков во все дни — показываем одной строкой без дней', () => {
    const everyDay = Object.fromEntries(
        ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map((d) => [d, WORKDAY]));
    assert.deepEqual(breakLines(everyDay), [{ days: null, time: '13:00–14:00' }]);
});

test('обед не у всех рабочих дней — перечисляем дни', () => {
    // В Костанае суббота короткая и без обеда: «13:00–14:00» без оговорки
    // означало бы, что в субботу тоже перерыв.
    assert.deepEqual(breakLines(KOSTANAY), [
        { days: 'Пн', time: '13:00–14:00' },
        { days: 'Вт', time: '13:00–14:00' },
        { days: 'Ср', time: '13:00–14:00' },
        { days: 'Чт', time: '13:00–14:00' },
        { days: 'Пт', time: '13:00–14:00' },
    ]);
});

test('без обеда строк нет', () => {
    assert.deepEqual(breakLines({ mon: { from: '09:00', to: '19:00' } }), []);
    assert.deepEqual(breakLines(null), []);
});
