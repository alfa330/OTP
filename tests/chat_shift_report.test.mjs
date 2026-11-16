import test from 'node:test';
import assert from 'node:assert/strict';

import {
    GROUP_ALL,
    ROW_ABSENT,
    ROW_NO_SHIFT,
    ROW_SHIFT,
    VIEW_PEOPLE,
    VIEW_SHIFTS,
    activePreset,
    chatsPerHour,
    csvFileName,
    earliestDay,
    endNote,
    filterRows,
    firstReply,
    formatDay,
    formatDuration,
    formatHms,
    formatRate,
    formatReply,
    groupOptions,
    hourBars,
    innerReply,
    journalBar,
    journalSegments,
    journalTotals,
    minutesLabel,
    nextSort,
    normText,
    overNorm,
    peopleColumns,
    peopleCsv,
    peopleRows,
    planLabel,
    presetRange,
    shiftColumns,
    shiftCsv,
    sortRows,
    spanOf,
    sumRows,
} from '../src/components/wazzup/shiftReportShared.js';

/*
 * «Операторы → Смены» в «Чатах ОП». Итоги по людям и по таблице складываются из сумм ответа, а не
 * усредняются средние; чатов в час — только из смен; фильтр, сортировка и выгрузка — одни и те же
 * строки, что на экране.
 */

const shift = (over = {}) => ({
    key: `shift:${over.userId ?? 10}:${over.start ?? '2026-11-12T09:00:00'}`,
    kind: ROW_SHIFT, userId: 10, name: 'Анна', groupKey: 'verifier', groupLabel: 'Верификаторы',
    date: '2026-11-12', start: '2026-11-12T09:00:00', end: '2026-11-12T18:00:00', open: false,
    endNote: null, plan: [{ start: '09:00', end: '18:00', nextDay: false, type: null }],
    lateSeconds: 0, earlySeconds: 0, active: 6 * 3600, breakTime: 3600, training: 1800, tech: 0,
    offline: 0, chats: 30, messages: 90, firstSum: 600, firstCount: 10, innerSum: 1200, innerCount: 12,
    chatsPerHour: 5, segments: [], hours: [],
    ...over,
});

const loose = (over = {}) => shift({
    kind: ROW_NO_SHIFT, userId: 40, name: 'Ольга', groupKey: 'osnova', groupLabel: 'Основа',
    start: '2026-11-12T10:00:00', end: '2026-11-12T15:00:00', active: 0, breakTime: 0, training: 0,
    chats: 8, messages: 9, firstSum: 300, firstCount: 5, innerSum: 400, innerCount: 8,
    chatsPerHour: null, plan: [], ...over,
});

const absent = (over = {}) => shift({
    kind: ROW_ABSENT, userId: 30, name: 'Вера', active: 0, breakTime: 0, training: 0, chats: 0,
    messages: 0, firstSum: 0, firstCount: 0, innerSum: 0, innerCount: 0, chatsPerHour: null, ...over,
});

test('форматы длительностей, ответа и дня', () => {
    assert.equal(formatDuration(0), '—');
    assert.equal(formatDuration(30), '< 1 м');
    assert.equal(formatDuration(45 * 60), '45 м');
    assert.equal(formatDuration(7 * 3600 + 34 * 60 + 59), '7 ч 34 м');
    assert.equal(formatDuration(2 * 3600), '2 ч');
    assert.equal(formatReply(null), '—');
    assert.equal(formatReply(45.4), '45 с');
    assert.equal(formatReply(80), '1 м 20 с');
    assert.equal(formatReply(240), '4 м');
    assert.equal(formatReply(8100), '2 ч 15 м');
    assert.equal(formatRate(6.25), '6,3');
    assert.equal(formatRate(null), '—');
    assert.equal(formatHms(3725), '1:02:05');
    assert.equal(formatDay('2026-11-15'), '15.11, вс');
    assert.equal(formatDay('2026-11-16'), '16.11, пн');
    assert.equal(minutesLabel(32 * 60), '32 мин');
    assert.equal(minutesLabel(97 * 60), '1 ч 37 мин');
    assert.equal(normText(60), '1 минута');
    assert.equal(normText(240), '4 минуты');
    assert.equal(normText(300), '5 минут');
    assert.equal(normText(90), '1 м 30 с');
});

test('период: быстрые кнопки и самый ранний день', () => {
    const today = new Date(2026, 10, 16);
    assert.deepEqual(presetRange('yesterday', today), { from: '2026-11-15', to: '2026-11-15' });
    assert.deepEqual(presetRange('week', today), { from: '2026-11-10', to: '2026-11-16' });
    assert.deepEqual(presetRange('month', today), { from: '2026-10-18', to: '2026-11-16' });
    assert.equal(activePreset({ from: '2026-11-10', to: '2026-11-16' }, today), 'week');
    assert.equal(activePreset({ from: '2026-11-11', to: '2026-11-16' }, today), null);
    assert.equal(earliestDay(today), '2026-10-03');
});

test('смена: конец следующего дня, идущая смена, пометка конца', () => {
    assert.deepEqual(spanOf(shift({ start: '2026-11-12T21:00:00', end: '2026-11-13T09:02:00' })),
        { start: '21:00', end: '09:02', nextDay: true });
    assert.deepEqual(spanOf(shift({ open: true, end: '2026-11-12T15:00:00' })),
        { start: '09:00', end: 'сейчас', nextDay: false });
    assert.equal(endNote(shift({ endNote: 'portal' })), 'портал закрыт');
    assert.equal(endNote(shift()), '');
    assert.equal(planLabel([{ start: '21:00', end: '09:00', nextDay: true, type: null },
        { start: '10:00', end: '12:00', type: 'phone_shift' }]), '21:00–09:00, 10:00–12:00 (телефон)');
});

test('средние ответа — из сумм; дольше нормы — повод покрасить', () => {
    assert.equal(firstReply(shift()), 60);
    assert.equal(innerReply(shift()), 100);
    assert.equal(firstReply(absent()), null);
    assert.equal(overNorm(61, 60), true);
    assert.equal(overNorm(60, 60), false);
    assert.equal(overNorm(null, 60), false);
    assert.equal(chatsPerHour(3, 14 * 60), null);
    assert.equal(chatsPerHour(10, 4 * 3600), 2.5);
});

test('фильтр группы и поиска; счётчики групп учитывают поиск', () => {
    const rows = [shift(), shift({ userId: 20, name: 'Борис', start: '2026-11-12T10:00:00' }), loose()];
    assert.equal(filterRows(rows, { group: 'osnova' }).length, 1);
    assert.equal(filterRows(rows, { query: 'бор' }).length, 1);
    assert.equal(filterRows(rows, { group: GROUP_ALL, query: '' }).length, 3);
    const groups = [{ key: 'verifier', label: 'Верификаторы' }, { key: 'osnova', label: 'Основа' }];
    assert.deepEqual(groupOptions(groups, rows, 'ан').map((o) => [o.value, o.count]),
        [['all', 1], ['verifier', 1], ['osnova', 0]]);
});

test('итог — суммы строк, чатов в час только из смен, не вышел — счётчиком', () => {
    const total = sumRows([shift(), shift({ start: '2026-11-13T09:00:00', date: '2026-11-13', chats: 10, active: 2 * 3600 }),
        loose(), absent()]);
    assert.equal(total.shifts, 2);
    assert.equal(total.noShiftDays, 1);
    assert.equal(total.absent, 1);
    assert.equal(total.chats, 48);
    assert.equal(total.active, 8 * 3600);
    // 40 чатов смен за 8 часов «Активного»: чаты без смены в норму часа не входят
    assert.equal(total.chatsPerHour, 5);
    assert.equal(firstReply(total), (600 + 600 + 300) / 25);
});

test('по сотрудникам: одна строка на человека, группа — с последнего дня', () => {
    const rows = [
        shift({ date: '2026-11-13', start: '2026-11-13T09:00:00', groupKey: 'potok', groupLabel: 'Поток' }),
        shift(), absent({ userId: 10 }), loose(),
    ];
    const people = peopleRows(rows);
    assert.equal(people.length, 2);
    const anna = people.find((person) => person.userId === 10);
    assert.equal(anna.shifts, 2);
    assert.equal(anna.absent, 1);
    assert.equal(anna.chats, 60);
    assert.equal(anna.groupKey, 'potok');
    assert.equal(anna.chatsPerHour, 5);
    const olga = people.find((person) => person.userId === 40);
    assert.equal(olga.noShiftDays, 1);
    assert.equal(olga.chatsPerHour, null);
    // итог по людям — то же, что итог по строкам
    assert.deepEqual(sumRows(people), sumRows(rows));
});

test('сортировка: пустые внизу, дата — свежие сверху, внутри дня по началу', () => {
    const a = shift({ date: '2026-11-12', start: '2026-11-12T15:00:00', name: 'Анна' });
    const b = shift({ date: '2026-11-12', start: '2026-11-12T09:00:00', name: 'Борис' });
    const c = shift({ date: '2026-11-13', start: '2026-11-13T09:00:00', name: 'Вера' });
    assert.deepEqual(sortRows([a, b, c], { key: 'date', dir: 'desc' }).map((r) => r.name), ['Вера', 'Борис', 'Анна']);
    const noRate = loose({ name: 'Ольга' });
    assert.deepEqual(sortRows([noRate, a, c], { key: 'chatsPerHour', dir: 'asc' }).map((r) => r.name).at(-1), 'Ольга');
    assert.deepEqual(sortRows([noRate, a, c], { key: 'chatsPerHour', dir: 'desc' }).map((r) => r.name).at(-1), 'Ольга');
    assert.deepEqual(sortRows([c, a], { key: 'name', dir: 'asc' }).map((r) => r.name), ['Анна', 'Вера']);
    assert.deepEqual(nextSort({ key: 'chats', dir: 'desc' }, 'chats'), { key: 'chats', dir: 'asc' });
    assert.deepEqual(nextSort({ key: 'chats', dir: 'desc' }, 'name'), { key: 'name', dir: 'asc' });
    assert.deepEqual(nextSort({ key: 'name', dir: 'asc' }, 'firstReply'), { key: 'firstReply', dir: 'desc' });
});

test('журнал смены: отрезки, полоса, итоги по видам, часы', () => {
    const row = shift({
        segments: [
            { kind: 'active', start: '2026-11-12T09:00:00', end: '2026-11-12T12:00:00', note: null },
            { kind: 'offline', start: '2026-11-12T12:00:00', end: '2026-11-12T12:30:00', note: 'portal' },
            { kind: 'active', start: '2026-11-12T12:30:00', end: '2026-11-12T23:30:00', note: null },
            { kind: 'break', start: '2026-11-12T23:30:00', end: '2026-11-13T00:30:00', note: null },
        ],
        hours: [{ hour: '2026-11-12T09:00', chats: 4, messages: 10 }, { hour: '2026-11-12T10:00', chats: 0, messages: 0 },
            { hour: '2026-11-12T11:00', chats: 8, messages: 20 }],
    });
    const segments = journalSegments(row);
    assert.equal(segments[1].note, 'портал закрыли — смену закрыл раздел');
    assert.equal(segments[1].seconds, 1800);
    assert.equal(segments[3].nextDay, true);
    const bar = journalBar(row);
    assert.equal(bar.length, 4);
    assert.ok(Math.abs(bar.reduce((sum, part) => sum + part.share, 0) - 1) < 1e-9);
    assert.deepEqual(journalTotals(row).map((t) => [t.kind, t.seconds]),
        [['active', 14 * 3600], ['break', 3600], ['offline', 1800]]);
    assert.deepEqual(hourBars(row).map((b) => [b.label, b.chats, b.share]), [['09', 4, 0.5], ['10', 0, 0], ['11', 8, 1]]);
    assert.deepEqual(journalBar(loose()), []);
});

test('колонки: подсказки с нормами сервера, порядок итогов совпадает с таблицей', () => {
    const shiftKeys = shiftColumns().map((c) => c.key);
    assert.deepEqual(shiftKeys, ['date', 'name', 'plan', 'shift', 'active', 'breakTime', 'training',
        'chats', 'chatsPerHour', 'firstReply', 'innerReply']);
    assert.deepEqual(peopleColumns().map((c) => c.key), ['name', 'shifts', 'active', 'breakTime', 'training',
        'chats', 'chatsPerHour', 'firstReply', 'innerReply']);
    const custom = shiftColumns({ firstReplySeconds: 120, innerReplySeconds: 300 });
    assert.match(custom.find((c) => c.key === 'firstReply').hint, /2 минуты/);
    assert.match(custom.find((c) => c.key === 'innerReply').hint, /5 минут/);
});

test('выгрузка: те же строки, длительности ЧЧ:ММ:СС, не вышел без цифр работы', () => {
    const csv = shiftCsv([shift({ lateSeconds: 1200, end: '2026-11-13T09:02:00', start: '2026-11-12T21:00:00' }), absent()]);
    assert.equal(csv.length, 3);
    assert.equal(csv[0].length, csv[1].length);
    const [, first, second] = csv;
    assert.equal(first[3], 'Смена');
    assert.equal(first[6], '09:02 (+1)');
    assert.equal(first[7], 20);
    assert.equal(first[9], '6:00:00');
    assert.equal(first[16], '5,0');
    assert.equal(first[18], 60);
    assert.equal(second[3], 'Не вышел');
    assert.equal(second[5], '');
    assert.equal(second[14], '');
    const people = peopleCsv(peopleRows([shift(), loose()]));
    assert.equal(people.length, 3);
    assert.equal(people[0].length, people[1].length);
    assert.equal(csvFileName(VIEW_PEOPLE, { from: '2026-11-10', to: '2026-11-16' }), 'chat_shifts_people_2026-11-10_2026-11-16.csv');
    assert.equal(csvFileName(VIEW_SHIFTS, { from: '2026-11-10', to: '2026-11-16' }), 'chat_shifts_shifts_2026-11-10_2026-11-16.csv');
});
