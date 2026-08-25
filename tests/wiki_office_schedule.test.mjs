import assert from 'node:assert/strict';
import test from 'node:test';

import {
    DEFAULT_BREAK,
    breakLines, buildSchedule, dayHoursOn, dayIndexOf, hasBreaks, officeNow,
    officeStatus, officeStatusOn, officeTodayISO,
    scheduleLines, setBreaks, untilText,
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

test('до открытия — сегодняшний день, сдвиг нулевой', () => {
    assert.deepEqual(officeStatus(KOSTANAY, at('2026-08-12T02:00:00Z')), // 07:00 среда
        { state: 'closed', opensAt: '09:00', opensCode: 'wed', opensIn: 0 });
});

test('после закрытия — ближайший рабочий день, а не завтрашний', () => {
    // Вечер субботы: воскресенье выходное, значит открытие в понедельник.
    assert.deepEqual(officeStatus(KOSTANAY, at('2026-08-15T15:00:00Z')), // 20:00 суббота
        { state: 'closed', opensAt: '09:00', opensCode: 'mon', opensIn: 2 });
});

test('в выходной день офис закрыт', () => {
    assert.deepEqual(officeStatus(KOSTANAY, at('2026-08-16T07:00:00Z')), // 12:00 воскресенье
        { state: 'closed', opensAt: '09:00', opensCode: 'mon', opensIn: 1 });
});

test('время берётся по Алматы, а не по машине пользователя', () => {
    // 22:00 UTC — по Гринвичу среда почти кончилась, в Алматы уже 03:00 четверга.
    const now = officeNow(at('2026-08-12T22:00:00Z'));
    assert.deepEqual(now, { dayIndex: 3, minutes: 180 });
    assert.equal(officeStatus(KOSTANAY, at('2026-08-12T22:00:00Z')).opensIn, 0);
});

/* Срок рядом со статусом — просьба задачи #236: «закрыт до завтра 10:00»,
 * «закрыт до 29.08». Формулировка «до …» одна на оба состояния: оператор
 * диктует водителю одну и ту же конструкцию, открыт офис или закрыт. */

test('срок словами: сегодня, завтра и день недели', () => {
    assert.equal(untilText(officeStatus(KOSTANAY, at('2026-08-12T06:20:00Z'))), 'до 19:00');
    assert.equal(untilText(officeStatus(KOSTANAY, at('2026-08-12T08:30:00Z'))), 'до 14:00');
    // 07:00 среды: откроется сегодня же.
    assert.equal(untilText(officeStatus(KOSTANAY, at('2026-08-12T02:00:00Z'))), 'до 09:00');
    // 12:00 воскресенья: завтра понедельник — пишем «завтра», а не «понедельника».
    assert.equal(untilText(officeStatus(KOSTANAY, at('2026-08-16T07:00:00Z'))), 'до завтра 09:00');
    // 20:00 субботы: до понедельника ещё два дня, «завтра» было бы неправдой.
    assert.equal(untilText(officeStatus(KOSTANAY, at('2026-08-15T15:00:00Z'))),
                 'до понедельника 09:00');
});

test('без графика срока нет — молчим, а не выдумываем', () => {
    assert.equal(untilText(officeStatus(null)), null);
    assert.equal(untilText(null), null);
    assert.equal(untilText({ state: 'closed' }), null);
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
    // Дни сворачиваются в серию: «обед Пн–Пт» вместо пяти одинаковых строк,
    // но суббота в эту серию не попадает — в ней перерыва нет.
    assert.deepEqual(breakLines(KOSTANAY), [{ days: 'Пн–Пт', time: '13:00–14:00' }]);
});

test('без обеда строк нет', () => {
    assert.deepEqual(breakLines({ mon: { from: '09:00', to: '19:00' } }), []);
    assert.deepEqual(breakLines(null), []);
});

/* Статус за календарный день — вторая, суточная мера того же графика. Она
 * нужна истории («работал ли офис 17 августа»), и у неё есть близнец на
 * сервере: schedule_state_on в wiki/offices.py. Разъехаться им нельзя, поэтому
 * набор случаев здесь и в tests/test_wiki_offices.py один и тот же. */

test('день недели считается по календарю, а не по зоне браузера', () => {
    // 2026-08-17 — понедельник, 2026-08-16 — воскресенье. Разбор даты по
    // местной зоне в западных поясах отдавал бы предыдущие сутки, и воскресный
    // выходной уезжал бы на субботу.
    assert.equal(dayIndexOf('2026-08-17'), 0);
    assert.equal(dayIndexOf('2026-08-16'), 6);
    assert.equal(dayIndexOf('17.08.2026'), null);
    assert.equal(dayIndexOf(''), null);
});

test('рабочий день — открыт, с часами этого дня', () => {
    assert.deepEqual(officeStatusOn(KOSTANAY, '2026-08-15'), // суббота, короткая
        { state: 'open', from: '10:00', until: '13:00' });
});

test('выходной день — закрыт', () => {
    assert.deepEqual(officeStatusOn(KOSTANAY, '2026-08-16'), { state: 'closed' });
});

test('график не заполнен — статуса нет, а не «закрыт»', () => {
    // Офис «ОНЛАЙН»: часов работы у него нет, и «Закрыт» про него неправда.
    assert.deepEqual(officeStatusOn(null, '2026-08-17'), { state: 'none' });
    assert.deepEqual(officeStatusOn({ mon: null, sun: null }, '2026-08-17'), { state: 'none' });
});

test('дата мусорная — статуса нет', () => {
    assert.deepEqual(officeStatusOn(KOSTANAY, 'позавчера'), { state: 'none' });
});

test('сегодня берётся по времени офиса', () => {
    // 2026-08-16 22:30 UTC — это уже 17 августа 03:30 по Алматы: у оператора в
    // браузере ещё воскресенье, а офис живёт понедельником.
    assert.equal(officeTodayISO(new Date('2026-08-16T22:30:00Z')), '2026-08-17');
});

test('разный обед — разные строки, соседние дни склеиваются', () => {
    const week = {
        mon: { from: '09:00', to: '19:00', break_from: '13:00', break_to: '14:00' },
        tue: { from: '09:00', to: '19:00', break_from: '13:00', break_to: '14:00' },
        wed: { from: '09:00', to: '19:00', break_from: '14:00', break_to: '15:00' },
        thu: { from: '09:00', to: '19:00' },
        fri: { from: '09:00', to: '19:00', break_from: '13:00', break_to: '14:00' },
        sat: null, sun: null,
    };
    assert.deepEqual(breakLines(week), [
        { days: 'Пн–Вт', time: '13:00–14:00' },
        { days: 'Ср', time: '14:00–15:00' },
        // Пятница отдельно: четверг без обеда, серия прерывается.
        { days: 'Пт', time: '13:00–14:00' },
    ]);
});

test('часы дня отдаются вместе с обедом', () => {
    assert.deepEqual(dayHoursOn(KOSTANAY, '2026-08-17'), // понедельник
        { from: '09:00', to: '19:00', breakFrom: '13:00', breakTo: '14:00' });
    // Суббота короткая и без обеда.
    assert.deepEqual(dayHoursOn(KOSTANAY, '2026-08-15'),
        { from: '10:00', to: '13:00', breakFrom: null, breakTo: null });
});

test('в выходной и без графика часов нет', () => {
    assert.equal(dayHoursOn(KOSTANAY, '2026-08-16'), null);
    assert.equal(dayHoursOn(null, '2026-08-17'), null);
    assert.equal(dayHoursOn(KOSTANAY, 'вчера'), null);
});

/* Тумблер обеда в форме офиса: правило целиком здесь, чтобы его можно было
 * проверить без JSX (так же, как правила номеров парка в parkPoints.js). */

test('обед в неделе виден, только когда набраны обе границы', () => {
    assert.equal(hasBreaks(KOSTANAY), true);
    // Суббота Костаная без обеда — сама по себе неделя без обеда.
    assert.equal(hasBreaks({ sat: { from: '10:00', to: '13:00' } }), false);
    // Одна граница — опечатка, а не перерыв: сервер её тоже не сохранит.
    assert.equal(hasBreaks({ mon: { from: '09:00', to: '19:00', break_from: '13:00' } }), false);
    assert.equal(hasBreaks({}), false);
    assert.equal(hasBreaks(null), false);
});

test('обед снимается со всей недели, выходные остаются выходными', () => {
    const off = setBreaks(KOSTANAY, false);
    assert.equal(hasBreaks(off), false);
    assert.deepEqual(off.mon, { from: '09:00', to: '19:00' });
    assert.deepEqual(off.sat, { from: '10:00', to: '13:00' });
    assert.equal(off.sun, null);
    // Часы недели не тронуты — снимали обед, а не график.
    assert.deepEqual(scheduleLines(off), scheduleLines(KOSTANAY));
});

test('обед ставится во все рабочие дни разом', () => {
    const on = setBreaks(setBreaks(KOSTANAY, false), true);
    assert.deepEqual(breakLines(on), [{ days: null, time: '13:00–14:00' }]);
    // Короткая суббота тоже рабочий день — обед достаётся и ей.
    assert.deepEqual(on.sat,
        { from: '10:00', to: '13:00', break_from: '13:00', break_to: '14:00' });
    assert.equal(on.sun, null);
});

test('обед не достаётся дню с недобранными часами', () => {
    const half = setBreaks({ mon: { from: '09:00' } }, true);
    assert.deepEqual(half.mon, { from: '09:00' });
});

test('пустой график тумблер не заполняет', () => {
    assert.deepEqual(setBreaks({}, true), {});
    assert.deepEqual(setBreaks(null, false), {});
});

test('время обеда по умолчанию — одно на форму', () => {
    const week = setBreaks({ mon: { from: '09:00', to: '18:00' } }, true);
    assert.deepEqual(week.mon, {
        from: '09:00', to: '18:00',
        break_from: DEFAULT_BREAK.from, break_to: DEFAULT_BREAK.to,
    });
});

test('пресет собирается и с обедом, и без него', () => {
    const days = ['mon', 'tue', 'wed', 'thu', 'fri'];
    const withBreak = buildSchedule({
        days, from: '09:00', to: '18:00',
        breakFrom: DEFAULT_BREAK.from, breakTo: DEFAULT_BREAK.to,
    });
    assert.deepEqual(scheduleLines(withBreak), [
        { days: 'Пн–Пт', time: '09:00–18:00', isDayOff: false },
        { days: 'Сб–Вс', time: 'выходной', isDayOff: true },
    ]);
    assert.deepEqual(breakLines(withBreak), [{ days: null, time: '13:00–14:00' }]);

    // Тумблер выключен — пресет отдаёт те же часы и ни одного обеда.
    const bare = buildSchedule({ days, from: '09:00', to: '18:00', breakFrom: null, breakTo: null });
    assert.deepEqual(scheduleLines(bare), scheduleLines(withBreak));
    assert.deepEqual(breakLines(bare), []);
});
