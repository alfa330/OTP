// «Воронка найма» (#404): форматирование и подписи раздела — hiringMeta.js.
// Время с сервера — наивное время Алматы: разбирается по цифрам строки, без поясов браузера.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    CHANNEL_COLORS, CHANNEL_COLOR_CODES, GROUP_CAPACITY_MAX, ICORE_PHONE_FRAME_ID, STAGES, STAGE_LABELS, FUNNEL_STAGES,
    SUBSTAGE_LABELS, TOUCH_RESULT_LABELS, addDays, buildICorePhoneUrl, channelTone, daysSince, groupNameAfterStart, groupNameFor,
    groupRequestBody, groupSeats, groupWhen, hiringTabs, joinDateTime, longTime, markGroupFull, matchesQuery, monthLabel,
    openICorePhoneCall, percent, plural, prettyPhone, reasonKindFor, shiftMonth, shortTime, splitDateTime, stepCapacity,
} from '../src/components/hiring/hiringMeta.js';

test('call goes to iCore Phone through a hidden frame, number as digits only', () => {
    // Формат — docs/CLICK-TO-CALL.md телефона: icore-phone:call?number=%2B<2–15 цифр> (плюс обязателен — линия Binotel), без префикса.
    assert.equal(buildICorePhoneUrl('+7 (705) 123-45-67'), 'icore-phone:call?number=%2B77051234567');
    assert.equal(buildICorePhoneUrl('x'), null);
    assert.equal(buildICorePhoneUrl('1234567890123456'), null);
    const frames = [];
    const doc = {
        body: { appendChild: (node) => frames.push(node) },
        getElementById: (id) => frames.find((node) => node.id === id) || null,
        createElement: () => ({ style: {}, setAttribute() {} }),
    };
    assert.equal(openICorePhoneCall('+77051234567', doc), 'icore-phone:call?number=%2B77051234567');
    assert.equal(frames.length, 1);
    assert.equal(frames[0].id, ICORE_PHONE_FRAME_ID);
    assert.equal(frames[0].src, 'icore-phone:call?number=%2B77051234567');
    assert.equal(frames[0].style.display, 'none');
    openICorePhoneCall('+77051234568', doc);
    assert.equal(frames.length, 1);                      // кадр один на страницу
    assert.equal(frames[0].src, 'icore-phone:call?number=%2B77051234568');
    assert.throws(() => openICorePhoneCall('нет номера', doc), /не разобран/);
});

test('ten stages in the order of the spec', () => {
    assert.equal(STAGES.length, 10);
    assert.deepEqual(STAGES.slice(0, 3), ['contacts', 'responses', 'in_work']);
    assert.equal(STAGE_LABELS.hired, 'Отработал 7 дней');
    assert.deepEqual(FUNNEL_STAGES, ['in_work', 'dialed', 'invited_office', 'invited_training', 'on_training', 'on_line', 'hired']);
});

test('times keep Almaty digits as they are', () => {
    assert.equal(shortTime('2026-10-30T14:05:00'), '30.10 14:05');
    assert.equal(shortTime('2026-10-30'), '30.10');
    assert.equal(shortTime(null), '');
    assert.equal(longTime('2026-10-30T14:05:00'), '30 октября, 14:05');
    assert.equal(longTime('2026-10-30'), '30 октября');
    assert.deepEqual(splitDateTime('2026-10-30T14:05:00'), { date: '2026-10-30', time: '14:05' });
    assert.equal(joinDateTime('2026-10-30', '09:30'), '2026-10-30T09:30');
    assert.equal(joinDateTime('', '09:30'), '');
});

test('month helpers', () => {
    assert.equal(monthLabel('2026-10'), 'Октябрь 2026');
    assert.equal(shiftMonth('2026-01', -1), '2025-12');
    assert.equal(shiftMonth('2026-12', 1), '2027-01');
    assert.equal(addDays('2026-10-30', 3), '2026-11-02');
    assert.equal(daysSince('2026-10-01', '2026-10-15'), 14);
    assert.equal(daysSince('2026-10-20T10:00:00', '2026-10-15'), -5);
});

test('phones, percents, plurals', () => {
    assert.equal(prettyPhone('+77051234567'), '+7 705 123 45 67');
    assert.equal(prettyPhone('x'), 'x');
    assert.equal(percent(33.3), '33,3 %');
    assert.equal(percent(null), '—');
    assert.equal(plural(1, 'смена', 'смены', 'смен'), 'смена');
    assert.equal(plural(3, 'смена', 'смены', 'смен'), 'смены');
    assert.equal(plural(11, 'смена', 'смены', 'смен'), 'смен');
    assert.equal(plural(25, 'смена', 'смены', 'смен'), 'смен');
});

test('reason kind follows initiator', () => {
    assert.equal(reasonKindFor('refused', 'ours'), 'refusal_ours');
    assert.equal(reasonKindFor('dropped', 'we'), 'drop_we');
    assert.equal(reasonKindFor('refused', 'self'), null);
});

test('client-side search matches name, phone and candidate number', () => {
    const card = { full_name: 'Иванов Иван', phone: '+77051234567', city: 'Алматы', candidate_no: 17 };
    assert.ok(matchesQuery(card, 'иван'));
    assert.ok(matchesQuery(card, '123 45'));
    assert.ok(matchesQuery(card, '17'));
    assert.ok(matchesQuery(card, ''));
    assert.ok(!matchesQuery(card, 'Астана'));
    assert.ok(!matchesQuery(card, '99'));
});

test('channel labels: palette from the owner (blue, red, pink) with whole Tailwind classes', () => {
    // Владелец 09.11.2026: Таргет — синий, hh — красный, Каспи — розовый; свои каналы — цветом из палитры.
    assert.deepEqual(CHANNEL_COLOR_CODES, ['red', 'orange', 'yellow', 'green', 'teal', 'blue', 'violet', 'pink', 'gray']);
    assert.equal(CHANNEL_COLORS.blue.dot, 'bg-blue-500');
    assert.equal(CHANNEL_COLORS.red.dot, 'bg-red-500');
    assert.equal(CHANNEL_COLORS.pink.dot, 'bg-pink-500');
    for (const code of CHANNEL_COLOR_CODES) {
        assert.ok(CHANNEL_COLORS[code].label && /^bg-\S+ text-\S+ ring-1 ring-\S+$/.test(CHANNEL_COLORS[code].pill), code);
    }
    assert.equal(channelTone('unknown'), CHANNEL_COLORS.gray);       // цвет из будущего — серый, не падение
});

test('group seats: how many more fit, full group, groups without capacity', () => {
    // Владелец 18.11.2026: рекрутер при выборе группы сразу видит, сколько ещё кандидатов в неё войдёт.
    assert.deepEqual(groupSeats({ capacity: 12, seats_taken: 8 }),
        { capacity: 12, taken: 8, free: 4, full: false, share: 8 / 12, label: 'свободно 4 из 12' });
    assert.deepEqual(groupSeats({ capacity: 3, seats_taken: 3 }), { capacity: 3, taken: 3, free: 0, full: true, share: 1, label: 'мест нет' });
    assert.equal(groupSeats({ capacity: 3, seats_taken: 5 }).free, 0);     // вместимость уменьшили раньше — не «-2»
    assert.equal(groupSeats({ capacity: 3, seats_taken: 5 }).share, 1);
    assert.equal(groupSeats({ capacity: '10', seats_taken: '4' }).label, 'свободно 6 из 10');
    // Группа до 18.11.2026: вместимости нет — предела нет, строка выбора не гаснет.
    assert.deepEqual(groupSeats({ capacity: null, seats_taken: 2 }),
        { capacity: null, taken: 2, free: null, full: false, share: 0, label: 'вместимость не указана' });
    assert.equal(groupSeats({}).label, 'вместимость не указана');
    assert.equal(groupSeats({ capacity: '', seats_taken: 1 }).capacity, null);
    assert.equal(groupSeats(undefined).full, false);
    assert.equal(GROUP_CAPACITY_MAX, 500);
});

test('group name follows the start date until the trainer types own', () => {
    assert.equal(groupNameFor('2026-11-24'), 'Группа 24.11');
    assert.equal(groupNameFor(''), '');
    assert.equal(groupNameFor(null), '');
    assert.equal(groupNameAfterStart('', '2026-11-24', false), 'Группа 24.11');
    assert.equal(groupNameAfterStart('Группа 24.11', '2026-12-01', false), 'Группа 01.12');
    assert.equal(groupNameAfterStart('Вечерняя', '2026-12-01', true), 'Вечерняя');   // своё название дата не затирает
});

test('tabs by rights: trainer starts with «Группы» and has no reports', () => {
    assert.deepEqual(hiringTabs({ groups: true, trainer: true }), ['groups', 'board']);
    assert.deepEqual(hiringTabs({ groups: true, dept_head: true }), ['groups', 'board', 'reports']);
    assert.deepEqual(hiringTabs({ recruiter: true, take: true }), ['desk', 'board', 'chats', 'reports']);
    assert.deepEqual(hiringTabs({ recruiter: true, lead: true, groups: true, upload: true, manage: true }),
        ['desk', 'board', 'chats', 'groups', 'reports', 'upload', 'settings']);
    // Руководитель рекрутинга без выдачи рекрутера (глава HR) — тоже отвечает кандидатам.
    assert.deepEqual(hiringTabs({ lead: true, manage: true }), ['board', 'chats', 'reports', 'settings']);
    assert.deepEqual(hiringTabs({ recruiter: true, groups: true }), ['desk', 'board', 'chats', 'groups', 'reports']);
    assert.deepEqual(hiringTabs({ dept_head: true }), ['board', 'reports']);
    assert.deepEqual(hiringTabs(), ['board']);
});

test('capacity stepper: «+» from empty puts the taken seats, «−» never goes below them', () => {
    assert.equal(stepCapacity('', 1, 0), '1');
    assert.equal(stepCapacity('', 1, 5), '5');
    assert.equal(stepCapacity('', -1, 5), '');
    assert.equal(stepCapacity('12', 1, 0), '13');
    assert.equal(stepCapacity('12', -1, 0), '11');
    assert.equal(stepCapacity('5', -1, 5), '5');
    assert.equal(stepCapacity('1', -1, 0), '1');
    assert.equal(stepCapacity(String(GROUP_CAPACITY_MAX), 1, 0), String(GROUP_CAPACITY_MAX));
    assert.equal(stepCapacity('', 1, 900), String(GROUP_CAPACITY_MAX));
});

test('group request body: trainer sends no trainer and department, lead does', () => {
    const form = { name: '  ', start_date: '2026-11-24', capacity: '12', vacancy_id: '3', trainer_id: '20', department_id: '367' };
    assert.deepEqual(groupRequestBody(form, { lead: false }),
        { name: 'Группа 24.11', start_date: '2026-11-24', capacity: 12, vacancy_id: 3 });
    assert.deepEqual(groupRequestBody({ ...form, name: 'Вечерняя', vacancy_id: '' }, { lead: true }),
        { name: 'Вечерняя', start_date: '2026-11-24', capacity: 12, vacancy_id: null, trainer_id: 20, department_id: 367 });
    assert.deepEqual(groupRequestBody({ ...form, trainer_id: '', department_id: '' }, { lead: true }),
        { name: 'Группа 24.11', start_date: '2026-11-24', capacity: 12, vacancy_id: 3, trainer_id: null, department_id: null });
    assert.equal(groupRequestBody({ capacity: '' }).capacity, 0);   // пустая вместимость — 0: отказ даст проверка окна
});

test('group date: upcoming start vs already running', () => {
    assert.equal(groupWhen({ start_date: '2026-11-24' }, '2026-11-18'), 'старт 24 ноября');
    assert.equal(groupWhen({ start_date: '2026-11-18' }, '2026-11-18'), 'старт 18 ноября');
    assert.equal(groupWhen({ start_date: '2026-11-05' }, '2026-11-18'), 'идёт с 5 ноября');
    assert.equal(groupWhen({}, '2026-11-18'), '');
});

test('«no seats» from the server greys the group at once, even if seats cannot be re-read', () => {
    const groups = [{ id: 1, capacity: 12, seats_taken: 11, seats_free: 1 }, { id: 2, capacity: null, seats_taken: 3 }];
    const next = markGroupFull(groups, 1);
    assert.equal(groupSeats(next[0]).full, true);
    assert.equal(next[0].seats_taken, 12);
    assert.equal(next[1], groups[1]);                        // остальные строки — те же объекты
    assert.deepEqual(markGroupFull(groups, 2), groups);      // группа без вместимости полной не бывает
    assert.deepEqual(markGroupFull(null, 1), []);
});

test('status «Недозвон / не ответил» covers WhatsApp, the call outcome stays «Недозвон»', () => {
    // Владелец 09.11.2026: статус — кандидат не ответил ни на звонок, ни на сообщение в WhatsApp.
    assert.equal(SUBSTAGE_LABELS.no_answer, 'Недозвон / не ответил');
    assert.equal(TOUCH_RESULT_LABELS.no_answer, 'Недозвон');
    assert.equal(TOUCH_RESULT_LABELS.dial, 'Дозвон');
});
