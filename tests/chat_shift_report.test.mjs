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
    clampRange,
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
    lateMinutes,
    minutesLabel,
    nextSort,
    normText,
    offlineNote,
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
    unlinkedText,
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
    // у границы часа минуты не превращаются в «60 м»
    assert.equal(formatReply(7170), '2 ч');
    assert.equal(formatReply(3630), '1 ч 1 м');
    assert.equal(formatRate(6.25), '6,3');
    assert.equal(formatRate(null), '—');
    assert.equal(formatHms(3725), '1:02:05');
    assert.equal(formatDay('2026-11-15'), '15.11, вс');
    assert.equal(formatDay('2026-11-16'), '16.11, пн');
    assert.equal(minutesLabel(32 * 60), '32 мин');
    assert.equal(minutesLabel(97 * 60), '1 ч 37 мин');
    // минуты опоздания — вверх, как в «Учёте часов» ((с + 59) // 60): 20 с — это 1 мин, не 0
    assert.equal(minutesLabel(20), '1 мин');
    assert.equal(minutesLabel(80), '2 мин');
    assert.equal(lateMinutes(0), 0);
    assert.equal(lateMinutes(60), 1);
    assert.equal(lateMinutes(61), 2);
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
    assert.equal(endNote(shift({ endNote: 'long' })), 'смену не закрыли');
    assert.equal(endNote(shift({ endNote: 'phone' })), 'по выходу из телефона');
    assert.equal(endNote(shift()), '');
    assert.equal(offlineNote(shift({ offline: 59 })), '');
    assert.equal(offlineNote(shift({ offline: 3600 })), 'вне смены 1 ч');
    assert.equal(offlineNote(loose({ offline: 3600 })), '');
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

test('переход «человек → его смены» — по id, однофамилец и «Сотрудник 123» не попадают', () => {
    const rows = [shift({ userId: 12, name: 'Сотрудник 12' }), shift({ userId: 123, name: 'Сотрудник 123' }),
        loose({ userId: 12, name: 'Сотрудник 12' })];
    assert.deepEqual(filterRows(rows, { query: 'Сотрудник 12' }).map((r) => r.userId), [12, 123, 12]);
    assert.deepEqual(filterRows(rows, { personId: 12 }).map((r) => r.userId), [12, 12]);
    assert.deepEqual(filterRows(rows, { personId: 0 }).map((r) => r.userId), []);
    const groups = [{ key: 'verifier', label: 'Верификаторы' }, { key: 'osnova', label: 'Основа' }];
    assert.deepEqual(groupOptions(groups, rows, '', 12).map((o) => [o.value, o.count]),
        [['all', 2], ['verifier', 1], ['osnova', 1]]);
});

test('плашка о непривязанных авторах согласована по числу', () => {
    assert.equal(unlinkedText({ authors: 1, messages: 1 }),
        '1 автор Wazzup не привязан к сотрудникам — 1 сообщение не вошло в отчёт.');
    assert.equal(unlinkedText({ authors: 21, messages: 21 }),
        '21 автор Wazzup не привязан к сотрудникам — 21 сообщение не вошло в отчёт.');
    assert.equal(unlinkedText({ authors: 3, messages: 34 }),
        '3 автора Wazzup не привязаны к сотрудникам — 34 сообщения не вошли в отчёт.');
    assert.equal(unlinkedText({ authors: 7, messages: 40 }),
        '7 авторов Wazzup не привязаны к сотрудникам — 40 сообщений не вошли в отчёт.');
});

test('период длиннее предела сервера сокращается до последних суток', () => {
    assert.deepEqual(clampRange({ from: '2026-11-10', to: '2026-11-16' }, 31),
        { range: { from: '2026-11-10', to: '2026-11-16' }, clipped: false });
    assert.deepEqual(clampRange({ from: '2026-10-05', to: '2026-11-15' }, 31),
        { range: { from: '2026-10-16', to: '2026-11-15' }, clipped: true });
    assert.deepEqual(clampRange({ from: '2026-10-16', to: '2026-11-15' }, 31).clipped, false);
    assert.deepEqual(clampRange({ from: '2026-10-15', to: '2026-11-15' }, 31).range.from, '2026-10-16');
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

test('по сотрудникам: опоздания и ранние уходы — сколько раз и сколько минут, каждый раз вверх', () => {
    const rows = [
        shift({ lateSeconds: 20 }),
        shift({ date: '2026-11-13', start: '2026-11-13T09:00:00', lateSeconds: 30 * 60, earlySeconds: 61 }),
        shift({ date: '2026-11-14', start: '2026-11-14T09:00:00' }),
    ];
    const [anna] = peopleRows(rows);
    assert.deepEqual([anna.lateCount, anna.lateMinutes, anna.earlyCount, anna.earlyMinutes], [2, 31, 1, 2]);
    assert.deepEqual(sumRows(peopleRows(rows)), sumRows(rows));
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
    // по возрастанию «Активного» прочерки «без смены» и «не вышел» не встают наверх нулями
    const short = shift({ name: 'Галина', active: 300 });
    assert.deepEqual(sortRows([loose({ name: 'Ольга' }), absent({ name: 'Вера' }), a, short],
        { key: 'active', dir: 'asc' }).map((r) => r.name), ['Галина', 'Анна', 'Вера', 'Ольга']);
    assert.deepEqual(sortRows([loose({ name: 'Ольга' }), short], { key: 'training', dir: 'asc' }).map((r) => r.name),
        ['Галина', 'Ольга']);
    // у строк людей — суммы, их ноль сортируется как число
    const people = peopleRows([loose({ userId: 40, name: 'Ольга' }), shift({ userId: 10, name: 'Анна' })]);
    assert.deepEqual(sortRows(people, { key: 'active', dir: 'asc' }).map((r) => r.name), ['Ольга', 'Анна']);
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
    assert.equal(segments[1].label, 'Вне смены');
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

/** Строки CSV — объектами {заголовок: значение}: колонка сверяется по смыслу, а не по номеру. */
const byHeader = (csv) => csv.slice(1).map((line) => Object.fromEntries(csv[0].map((title, index) => [title, line[index]])));

test('выгрузка по сменам: каждая колонка под своим заголовком', () => {
    const [first, second] = byHeader(shiftCsv([
        shift({ lateSeconds: 1200, earlySeconds: 20, end: '2026-11-13T09:02:00', start: '2026-11-12T21:00:00',
            tech: 600, offline: 900, endNote: 'portal' }),
        absent(),
    ]));
    assert.deepEqual(first, {
        'Дата': '2026-11-12', 'Менеджер': 'Анна', 'Группа': 'Верификаторы', 'Строка': 'Смена',
        'График': '09:00–18:00', 'Начало': '21:00', 'Конец': '09:02 (+1)', 'Опоздание, мин': 20,
        'Ранний уход, мин': 1, 'Конец смены': 'портал закрыт', 'Активный': '6:00:00', 'Перерыв': '1:00:00',
        'Тренинг': '0:30:00', 'Тех. пауза': '0:10:00', 'Вне смены': '0:15:00', 'Чаты': 30, 'Сообщения': 90,
        'Чатов в час': '5,0', 'Первых ответов': 10, 'Первый ответ, с': 60, 'Ответ в чате, с': 100,
    });
    assert.deepEqual(second, {
        'Дата': '2026-11-12', 'Менеджер': 'Вера', 'Группа': 'Верификаторы', 'Строка': 'Не вышел',
        'График': '09:00–18:00', 'Начало': '', 'Конец': '', 'Опоздание, мин': '', 'Ранний уход, мин': '',
        'Конец смены': '', 'Активный': '', 'Перерыв': '', 'Тренинг': '', 'Тех. пауза': '', 'Вне смены': '',
        'Чаты': '', 'Сообщения': '', 'Чатов в час': '', 'Первых ответов': '', 'Первый ответ, с': '',
        'Ответ в чате, с': '',
    });
});

test('выгрузка по сотрудникам: каждая колонка под своим заголовком', () => {
    const rows = peopleRows([shift({ lateSeconds: 90, tech: 600, offline: 120 }), loose()]);
    const anna = byHeader(peopleCsv(rows)).find((line) => line['Менеджер'] === 'Анна');
    assert.deepEqual(anna, {
        'Менеджер': 'Анна', 'Группа': 'Верификаторы', 'Смен': 1, 'Опозданий': 1, 'Опоздания всего, мин': 2,
        'Ранних уходов': 0, 'Ранние уходы всего, мин': 0, 'Дней без смены': 0, 'Не вышел': 0,
        'Активный': '6:00:00', 'Перерыв': '1:00:00', 'Тренинг': '0:30:00', 'Тех. пауза': '0:10:00',
        'Вне смены': '0:02:00', 'Чаты': 30, 'Сообщения': 90, 'Чатов в час': '5,0', 'Первых ответов': 10,
        'Первый ответ, с': 60, 'Ответ в чате, с': 100,
    });
});

test('выгрузка: имя файла по виду и периоду', () => {
    assert.equal(csvFileName(VIEW_PEOPLE, { from: '2026-11-10', to: '2026-11-16' }), 'chat_shifts_people_2026-11-10_2026-11-16.csv');
    assert.equal(csvFileName(VIEW_SHIFTS, { from: '2026-11-10', to: '2026-11-16' }), 'chat_shifts_shifts_2026-11-10_2026-11-16.csv');
});
