// «Воронка найма» (#404): форматирование и подписи раздела — hiringMeta.js.
// Время с сервера — наивное время Алматы: разбирается по цифрам строки, без поясов браузера.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    GROUP_CAPACITY_MAX, ICORE_PHONE_FRAME_ID, SOURCE_COLORS, SOURCE_COLOR_CODES, STAGES, STAGE_LABELS, FUNNEL_STAGES,
    SUBSTAGE_LABELS, TOUCH_RESULT_LABELS, addDays, boardQuery, buildICorePhoneUrl, daysSince, groupCards, groupKey,
    groupNameAfterStart, groupNameFor, groupRequestBody, groupSeats, groupWhen, hiringTabs, joinDateTime, longTime, markGroupFull,
    matchesQuery, monthLabel, openICorePhoneCall, percent, plural, prettyPhone, reasonKindFor, shiftMonth, shortTime, sourceChoices,
    sourceRequestBody, sourceTone, splitDateTime, stepCapacity, toggleInList, withExpanded, ARCHIVE_STAGE, columnCollapsed,
    DATED_STAGES, dayKey, dayLabel, isPastDay, openDaysFrom, visitTime, withExpandedDay,
    SYSTEM_TOUCH_LABELS, canReschedule, rescheduleBody, rescheduleProblem, trainingDay,
    HH_NO_ICORE, hhIcoreOptions, hhIcoreValue, hhSettingsBody, hhVacancyMeta,
    hhMoney, hhPeriod, hhResumeSummary, hhSpan,
} from '../src/components/hiring/hiringMeta.js';

test('hh.kz resume in the card: money, spans, periods and a one-line summary', () => {
    // Владелец 23.11.2026: «все резюме и письмо человека который обратился отобразились сразу в карточке».
    assert.equal(hhMoney({ amount: 250000, currency: 'KZT', sign: '₸' }), '250 000 ₸');
    assert.equal(hhMoney({ amount: 900, currency: 'USD' }), '900 USD');
    assert.equal(hhMoney(null), '');
    assert.equal(hhSpan({ years: 5, months: 3 }), '5 лет 3 мес.');
    assert.equal(hhSpan({ years: 1, months: 0 }), '1 год');
    assert.equal(hhSpan({ years: 0, months: 8 }), '8 мес.');
    assert.equal(hhSpan(null), '');
    assert.equal(hhPeriod('2020-01-01', null), 'янв 2020 — сейчас');
    assert.equal(hhPeriod('2018-03-01', '2019-12-01'), 'мар 2018 — дек 2019');
    assert.equal(hhPeriod(null, '2019-12-01'), '');
    assert.equal(hhResumeSummary({ title: 'Оператор call-центра', salary: { amount: 250000, sign: '₸' },
        experience_total: { years: 5, months: 3 } }), 'Оператор call-центра · 250 000 ₸ · опыт 5 лет 3 мес.');
    assert.equal(hhResumeSummary({ title: 'Стажёр', experience: [] }), 'Стажёр · без опыта');
    assert.equal(hhResumeSummary(null), '');
});

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

test('ten stages in the order of the spec and the «no response» archive last', () => {
    assert.equal(STAGES.length, 11);
    assert.deepEqual(STAGES.slice(0, 3), ['contacts', 'responses', 'in_work']);
    assert.equal(STAGE_LABELS.hired, 'Отработал 7 дней');
    assert.equal(STAGES[10], 'no_response');
    assert.equal(STAGE_LABELS.no_response, 'Не отвечает звонок/чат');
    // «Отработал 10 дней» (SZOV-10) — категория тренера: строка воронки и подпись есть, этапа — нет.
    assert.deepEqual(FUNNEL_STAGES, ['in_work', 'dialed', 'invited_office', 'invited_training', 'on_training', 'on_line', 'hired', 'hired_10']);
    assert.equal(STAGE_LABELS.hired_10, 'Отработал 10 дней');
    assert.ok(!STAGES.includes('hired_10'));
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

test('source colors: palette from the owner (blue, red, pink) with whole Tailwind classes', () => {
    // Владелец 20.11.2026: каналы слиты с источниками, цвет выбирается в источниках; стартовые — Таргет синий, hh красный,
    // Каспи розовый (его слова 09.11.2026). Источник без цвета — серый.
    assert.deepEqual(SOURCE_COLOR_CODES, ['red', 'orange', 'yellow', 'green', 'teal', 'blue', 'violet', 'pink', 'gray']);
    assert.equal(SOURCE_COLORS.blue.dot, 'bg-blue-500');
    assert.equal(SOURCE_COLORS.red.dot, 'bg-red-500');
    assert.equal(SOURCE_COLORS.pink.dot, 'bg-pink-500');
    for (const code of SOURCE_COLOR_CODES) {
        assert.ok(SOURCE_COLORS[code].label && /^bg-\S+ text-\S+ ring-1 ring-\S+$/.test(SOURCE_COLORS[code].pill), code);
    }
    assert.equal(sourceTone('unknown'), SOURCE_COLORS.gray);       // цвет из будущего — серый, не падение
    assert.equal(sourceTone(null), SOURCE_COLORS.gray);            // источник без цвета
});

test('card source choices: same kind, switched on, directory order', () => {
    const sources = [
        { id: 1, kind: 'responses', name: 'HeadHunter', is_active: true },
        { id: 4, kind: 'responses', name: 'Таргет', is_active: true },
        { id: 9, kind: 'responses', name: 'Старый', is_active: false },
        { id: 7, kind: 'contacts', name: 'База бывших водителей', is_active: true },
        { id: 10, kind: 'responses', name: 'WhatsApp' },                      // рекрутеру is_active не приходит — включён
    ];
    assert.deepEqual(sourceChoices({ kind: 'response' }, sources).map((s) => s.id), [1, 4, 10]);
    assert.deepEqual(sourceChoices({ kind: 'contact' }, sources).map((s) => s.id), [7]);
    assert.deepEqual(sourceChoices({}, sources).map((s) => s.id), [1, 4, 10]);
    assert.deepEqual(sourceChoices({ kind: 'response' }, null), []);
});

test('board query: one period for all columns, empty filters are not sent', () => {
    // Владелец 24.11.2026: «сделать пикер периода общим на все столбцы» — общие from / to (по дате отклика), а не
    // прежние responses_from / responses_to, которые резали только «Отклики».
    assert.deepEqual(boardQuery(), {});
    assert.deepEqual(boardQuery({ mine: true, query: '  Асель ', filters: { vacancy_id: '3', city: '', source_id: '4', recruiter_id: '' } }),
        { mine: '1', q: 'Асель', vacancy_id: '3', source_id: '4' });
    const withPeriod = boardQuery({ period: { from: '2026-11-12', to: '2026-11-18' } });
    assert.deepEqual(withPeriod, { from: '2026-11-12', to: '2026-11-18' });
    assert.ok(!('responses_from' in withPeriod) && !('responses_to' in withPeriod));
    assert.deepEqual(boardQuery({ period: { from: '', to: '2026-11-18' } }), { to: '2026-11-18' });
    // Остальные отборы доски уходят как были: город и рекрутер тоже.
    assert.deepEqual(boardQuery({ filters: { city: 'Алматы', recruiter_id: '10' } }), { city: 'Алматы', recruiter_id: '10' });
    // «Показать ещё» — только числовые id источников; группа без источника не раскрывается.
    assert.deepEqual(boardQuery({ expanded: ['4', 'none', '10', 'x1'] }), { expand: '4,10' });
    assert.deepEqual(boardQuery({ expanded: ['none'] }), {});
});

test('collapsed groups: open goes with every board request, «-» when nothing is open', () => {
    // Решение владельца 20.11.2026: группы «Откликов» свёрнуты, пока их не раскрыли — карточки сервер шлёт только раскрытым.
    assert.deepEqual(boardQuery({ open: [] }), { open: '-' });
    assert.deepEqual(boardQuery({ open: ['4', 'none', 'x', '10'] }), { open: '4,none,10' });
    assert.ok(!('open' in boardQuery({})));                           // без списка — сервер отдаёт карточки всем, как раньше
    const cards = new Map([[1, { id: 1 }], [2, { id: 2 }], [5, { id: 5 }]]);
    assert.deepEqual(groupCards({ card_ids: [2, 1, 9] }, cards), [{ id: 2 }, { id: 1 }]);   // пропавшую (9) — без дыр
    assert.deepEqual(groupCards({}, cards), []);
    assert.deepEqual(withExpanded(['4'], { source_id: 10 }), ['4', '10']);
    assert.deepEqual(withExpanded(['4'], { source_id: 4 }), ['4']);
    assert.deepEqual(withExpanded(['4'], { source_id: null }), ['4']);
});

test('invited columns by day: open and expanded days go with the board request', () => {
    // Владелец 24.11.2026: «Приглашен в офис» и «Приглашен на обучение» — по дням, раскрываются как группы «Откликов».
    assert.deepEqual(DATED_STAGES, ['invited_office', 'invited_training']);
    assert.deepEqual(boardQuery({ openDays: { invited_office: ['2026-11-24', 'none', 'x', '24.11.2026'] } }),
        { office_open: '2026-11-24,none', training_open: '-' });
    assert.deepEqual(boardQuery({ openDays: {} }), { office_open: '-', training_open: '-' });
    assert.ok(!('office_open' in boardQuery({})));                    // без openDays — сервер отдаёт карточки всем дням
    assert.deepEqual(boardQuery({ expandedDays: { invited_training: ['2026-11-30', 'none'] } }), { training_expand: '2026-11-30' });
    assert.equal(dayKey({ day: '2026-11-24' }), '2026-11-24');
    assert.equal(dayKey({ day: null }), 'none');
    assert.deepEqual(withExpandedDay(['2026-11-24'], { day: '2026-11-25' }), ['2026-11-24', '2026-11-25']);
    assert.deepEqual(withExpandedDay(['2026-11-24'], { day: '2026-11-24' }), ['2026-11-24']);
    assert.deepEqual(withExpandedDay(['2026-11-24'], { day: null }), ['2026-11-24']);   // без даты раскрывать нечем
    assert.deepEqual(withExpandedDay(undefined, { day: '2026-11-25' }), ['2026-11-25']);
    // Из браузера — только свои этапы и разборчивые ключи; прошедшие дни не копятся без конца.
    assert.deepEqual(openDaysFrom({ invited_office: ['2026-11-24', 'none', 5, 'x'], responses: ['4'] }),
        { invited_office: ['2026-11-24', 'none'], invited_training: [] });
    assert.deepEqual(openDaysFrom(null), { invited_office: [], invited_training: [] });
    assert.deepEqual(openDaysFrom('[]'), { invited_office: [], invited_training: [] });
    const many = Array.from({ length: 70 }, (_, index) => addDays('2026-01-01', index));
    assert.equal(openDaysFrom({ invited_office: many }).invited_office.length, 60);
    assert.equal(openDaysFrom({ invited_office: many }).invited_office.at(-1), many.at(-1));
});

test('day headers: today, tomorrow, yesterday, weekday; past days; visit time on the card', () => {
    const today = '2026-11-24';                                       // вторник
    assert.equal(dayLabel('2026-11-24', today), 'Сегодня, 24 ноября');
    assert.equal(dayLabel('2026-11-25', today), 'Завтра, 25 ноября');
    assert.equal(dayLabel('2026-11-23', today), 'Вчера, 23 ноября');
    assert.equal(dayLabel('2026-11-26', today), 'Чт, 26 ноября');
    assert.equal(dayLabel('2026-11-30', today), 'Пн, 30 ноября');
    assert.equal(dayLabel('2026-11-20', today), 'Пт, 20 ноября');
    assert.equal(dayLabel('2027-01-05', today), 'Вт, 5 января 2027');  // другой год — с годом
    assert.equal(dayLabel('2026-12-31', '2027-01-01'), 'Вчера, 31 декабря');
    assert.equal(dayLabel(null, today), 'Без даты');
    assert.equal(dayLabel('2026-11-26', 'мусор'), 'Чт, 26 ноября 2026');
    assert.equal(isPastDay('2026-11-23', today), true);
    assert.equal(isPastDay('2026-11-24', today), false);
    assert.equal(isPastDay('2026-12-01', today), false);
    assert.equal(isPastDay(null, today), false);
    assert.equal(visitTime({ office_visit_at: '2026-11-24T14:00:00' }), '14:00');
    assert.equal(visitTime({ office_visit_at: '2026-11-24' }), '');
    assert.equal(visitTime({}), '');
});

test('reschedule: who may, what the form needs, what goes to the server', () => {
    // Владелец 24.11.2026: перенос даты визита и обучения с причиной («форс-мажор, поменялись планы, учёба, другое с комментом»).
    const office = { stage: 'invited_office', is_closed: false, office_visit_at: '2026-11-25T10:00:00' };
    const training = { stage: 'invited_training', is_closed: false, group_start_date: '2026-11-25', training_date: null };
    assert.equal(canReschedule(office, true), true);
    assert.equal(canReschedule(training, true), true);
    assert.equal(canReschedule(office, false), false);                  // не закреплённый рекрутер и не руководитель
    assert.equal(canReschedule({ ...office, is_closed: true }, true), false);
    assert.equal(canReschedule({ stage: 'in_work' }, true), false);
    assert.equal(canReschedule(null, true), false);
    assert.equal(trainingDay(training), '2026-11-25');
    assert.equal(trainingDay({ ...training, training_date: '2026-11-27' }), '2026-11-27');
    assert.equal(trainingDay({}), '');
    const reasons = [{ code: 'plans', needs_comment: false }, { code: 'other', needs_comment: true }];
    assert.equal(rescheduleProblem({ date: '', reason: 'plans' }, true, reasons), 'Укажите новую дату и время визита');
    assert.equal(rescheduleProblem({ date: '', reason: 'plans' }, false, reasons), 'Укажите новую дату обучения');
    assert.equal(rescheduleProblem({ date: '2026-11-27', time: '14:00', reason: '' }, true, reasons), 'Укажите причину переноса');
    assert.equal(rescheduleProblem({ date: '2026-11-27', time: '14:00', reason: 'other', comment: '  ' }, true, reasons), 'Для «Другое» нужен комментарий');
    assert.equal(rescheduleProblem({ date: '2026-11-25', time: '10:00', reason: 'plans' }, true, reasons, office.office_visit_at),
        'Это та же дата — выберите новую');
    assert.equal(rescheduleProblem({ date: '2026-11-25', time: '11:00', reason: 'plans' }, true, reasons, office.office_visit_at), '');
    assert.equal(rescheduleProblem({ date: '2026-11-25', reason: 'plans' }, false, reasons, '2026-11-25'), 'Это та же дата — выберите новую');
    assert.equal(rescheduleProblem({ date: '2026-11-26', reason: 'other', comment: 'Сессия' }, false, reasons, '2026-11-25'), '');
    assert.deepEqual(rescheduleBody({ date: '2026-11-27', time: '14:00', reason: 'plans', comment: '  ' }, true),
        { when: '2026-11-27T14:00', reason: 'plans', comment: null });
    assert.deepEqual(rescheduleBody({ date: '2026-11-27', time: '14:00', reason: 'other', comment: ' Сессия ' }, false),
        { when: '2026-11-27', reason: 'other', comment: 'Сессия' });
    assert.equal(SYSTEM_TOUCH_LABELS.rescheduled, 'Перенос даты');
});

test('source window body: new source has a kind, existing one sends only name and color', () => {
    assert.deepEqual(sourceRequestBody({ name: '  Instagram ', kind: 'contacts', color: 'violet' }),
        { name: 'Instagram', color: 'violet', kind: 'contacts' });
    assert.deepEqual(sourceRequestBody({ name: 'Таргет', kind: 'responses', color: 'green' }, { id: 4, kind: 'responses' }),
        { name: 'Таргет', color: 'green' });
    assert.deepEqual(sourceRequestBody({ name: 'X', kind: 'weird' }), { name: 'X', color: null, kind: 'responses' });
});

test('collapsed lists toggle without touching the original; group keys', () => {
    const list = ['in_work'];
    assert.deepEqual(toggleInList(list, 'refused'), ['in_work', 'refused']);
    assert.deepEqual(toggleInList(list, 'in_work'), []);
    assert.deepEqual(list, ['in_work']);
    assert.deepEqual(toggleInList(null, '4'), ['4']);
    assert.equal(groupKey({ source_id: 4 }), '4');
    assert.equal(groupKey({ source_id: null }), 'none');
    assert.equal(groupKey({}), 'none');
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

test('«no response» archive column is collapsed until opened, other columns until collapsed', () => {
    assert.equal(ARCHIVE_STAGE, 'no_response');
    assert.equal(columnCollapsed('no_response', [], []), true);           // по умолчанию свёрнут
    assert.equal(columnCollapsed('no_response', null, undefined), true);
    assert.equal(columnCollapsed('no_response', ['no_response'], []), true);   // старый список свёрнутых его не раскрывает
    assert.equal(columnCollapsed('no_response', [], ['no_response']), false);
    assert.equal(columnCollapsed('refused', ['refused'], []), true);
    assert.equal(columnCollapsed('refused', [], ['refused']), false);
    assert.equal(columnCollapsed('in_work', null, null), false);
});

test('hh.kz vacancies: a toggle per vacancy, an iCORE vacancy only for tracked ones', () => {
    // Владелец 23.11.2026: «Добавь в настройки возможность выбрать какие вакансии отслеживать»; новые вакансии hh идут сами.
    const icore = [{ id: 1, name: 'Оператор', is_active: true }, { id: 2, name: 'Закрытая', is_active: false }];
    const options = hhIcoreOptions(icore);
    assert.deepEqual(options, [{ value: '1', label: 'Оператор' }]);       // не связана — серая подсказка, без пункта «нет»
    assert.deepEqual(hhIcoreOptions(icore, true), [{ value: '', label: HH_NO_ICORE }, { value: '1', label: 'Оператор' }]);
    assert.equal(hhIcoreValue(1, options), '1');
    assert.equal(hhIcoreValue(null, options), null);
    assert.equal(hhIcoreValue(2, options), null);         // выключенную вакансию iCORE сервер отклику не ставит — и здесь её нет
    // Правка одной вакансии: правила остальных (и тех, кого мост сейчас не видит) не теряются; выключение связь с
    // вакансией iCORE не стирает — включили снова, она на месте.
    assert.deepEqual(hhSettingsBody({ 7: { on: false }, 8: { on: true, vacancy_id: 1, since: '2026-11-24T10:15:00' } }, '8',
        { on: false, vacancy_id: 1 }), {
        vacancies: { 7: { on: false, vacancy_id: null }, 8: { on: false, vacancy_id: 1 } },
    });
    assert.deepEqual(hhSettingsBody({ 7: { on: false } }, '9', { on: true, vacancy_id: null }), {
        vacancies: { 7: { on: false, vacancy_id: null }, 9: { on: true, vacancy_id: null } },
    });
    assert.deepEqual(hhSettingsBody(null, null, null), { vacancies: {} });
    // Подпись: город и отклики; у включённой позже — с какого момента.
    assert.equal(hhVacancyMeta({ area: 'Алматы', responses: 153, on: true }), 'Алматы · откликов 153');
    assert.equal(hhVacancyMeta({ area: 'Алматы', responses: 251, on: true, tracked_since: '2026-11-24T10:15' }),
        'Алматы · откликов 251 · отслеживается с 24.11 10:15');
    assert.equal(hhVacancyMeta({ area: null, responses: null, on: false, tracked_since: '2026-11-24T10:15' }), '');
});
