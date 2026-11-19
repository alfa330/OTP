// Табель фронт-офисов в «Учете часов»: кому плитка и страница, запрос, имя файла.
// Решение владельца 06.11.2026: выгружают глава фронт-офисов и суперадмины.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    TIMESHEET_NO_CITY,
    TIMESHEET_PERIOD_HINT,
    TIMESHEET_SCOPE,
    canExportFrontOfficeTimesheet,
    downloadFileName,
    emptyTimesheetFilters,
    hasTimesheetFilters,
    headsFrontOfficeDepartment,
    isValidTimesheetPeriod,
    periodMaxEnd,
    periodMinStart,
    periodMonth,
    pruneTimesheetFilters,
    sameTimesheetFilters,
    shiftTimesheetPeriod,
    TIMESHEET_LEGEND,
    timesheetAvailable,
    timesheetCellTitle,
    timesheetCellView,
    timesheetColumnTotals,
    timesheetFileName,
    TIMESHEET_PERIOD_BUTTON_WIDTH,
    timesheetPeriodChip,
    timesheetPeriodFieldLabel,
    timesheetPeriodKey,
    timesheetPeriodPresets,
    timesheetPeriodTitle,
    timesheetQuery,
    timesheetSelectOptions,
    timesheetSpanLimit,
    timesheetWithoutCard,
    wholeMonthOfPeriod,
    wholeMonthPeriod,
} from '../src/components/hours/frontOfficeTimesheet.js';
import { TIMESHEET_OPTION_VALUE, buildHoursReportOptions } from '../src/components/hours/hoursReportOptions.js';

const frontOfficeHead = { id: 532, role: 'admin', headed_department_codes: ['front_office'] };
const singleFieldHead = { id: 533, role: 'admin', headed_department_code: 'FRONT_OFFICE' };
const superAdmin = { id: 2, role: 'super_admin' };
const opHead = { id: 313, role: 'admin', headed_department_codes: ['op'] };
const globalAdmin = { id: 9, role: 'admin' };
const frontOfficeSv = { id: 10, role: 'sv', department_code: 'front_office' };

test('выгружают только глава фронт-офисов и суперадмины', () => {
    assert.equal(canExportFrontOfficeTimesheet(frontOfficeHead), true);
    assert.equal(canExportFrontOfficeTimesheet(singleFieldHead), true);
    assert.equal(canExportFrontOfficeTimesheet(superAdmin), true);
    assert.equal(canExportFrontOfficeTimesheet({ id: 2, role: 'Super Admin' }), true);
    for (const outsider of [opHead, globalAdmin, frontOfficeSv, { id: 3, role: 'operator' }, null]) {
        assert.equal(canExportFrontOfficeTimesheet(outsider), false, JSON.stringify(outsider));
    }
    assert.equal(headsFrontOfficeDepartment(frontOfficeSv), false);
});

test('табель на экране — при группе фронт-офисов, у главы фронт-офисов и без группы', () => {
    const regions = { id: 37, department_id: 909 };
    const sales = { id: 5, department_id: 3 };
    const base = { canExport: true, departmentId: 909 };
    assert.equal(timesheetAvailable({ ...base, selectedGroupId: '37', selectedGroup: regions }), true);
    assert.equal(timesheetAvailable({ ...base, selectedGroupId: '5', selectedGroup: sales }), false);
    // Группа ещё не подгрузилась — не угадываем.
    assert.equal(timesheetAvailable({ ...base, selectedGroupId: '37', selectedGroup: null }), false);
    // Глава фронт-офисов без выбранной группы: её раздел и есть этот отдел.
    assert.equal(timesheetAvailable({ ...base, selectedGroupId: '', headedDepartmentId: 909 }), true);
    assert.equal(timesheetAvailable({ ...base, selectedGroupId: '', headedDepartmentId: 3 }), false);
    assert.equal(timesheetAvailable({ ...base, selectedGroupId: '' }), false);
    // Без права или пока отдел не известен — нет.
    assert.equal(timesheetAvailable({ canExport: false, departmentId: 909, selectedGroupId: '37', selectedGroup: regions }), false);
    assert.equal(timesheetAvailable({ canExport: true, departmentId: null, selectedGroupId: '37', selectedGroup: regions }), false);
});

test('ячейка дня: рабочий день нейтральный, отсутствия — своим тоном, точка — неполная отметка', () => {
    assert.deepEqual(timesheetCellView({ code: 'р', late: 12, early: 0, incomplete: true }),
        { label: 'р', tone: 'work', incomplete: true, late: 12, early: 0 });
    assert.equal(timesheetCellView({ code: 'в' }).tone, 'off');
    assert.equal(timesheetCellView({ code: 'Б/С' }).tone, 'bs');
    assert.equal(timesheetCellView({ code: 'О' }).tone, 'vacation');
    assert.equal(timesheetCellView({ code: 'Б' }).tone, 'sick');
    assert.equal(timesheetCellView({ code: 'н' }).tone, 'absent');
    assert.equal(timesheetCellView({ code: '' }).tone, 'empty');
    assert.equal(timesheetCellView({ code: 'н', incomplete: true }).incomplete, false);
});

test('подсказка ячейки: дата, день, отметки и минуты', () => {
    const day = { date: '2026-10-14', weekday: 'ср', future: false, holiday: false };
    assert.equal(
        timesheetCellTitle(day, { code: 'р', late: 12, early: 30, arrive: '09:12', leave: '18:30' }),
        '14 октября, ср · рабочий день · отметки 09:12–18:30 · опоздание 12 мин · ранний уход 30 мин',
    );
    assert.equal(
        timesheetCellTitle(day, { code: 'р', incomplete: true, arrive: '09:02', leave: null }),
        '14 октября, ср · рабочий день · отметки 09:02–— · нет отметки ухода',
    );
    assert.equal(timesheetCellTitle({ ...day, future: true }, { code: '' }), '14 октября, ср · ещё не наступил');
    assert.equal(timesheetCellTitle({ ...day, holiday: true }, { code: 'в' }), '14 октября, ср · выходной · праздник');
});

test('итоги таблицы: отработавшие по дням и суммы колонок', () => {
    const payload = {
        days: [{ date: '2026-10-01' }, { date: '2026-10-02' }],
        cities: [
            { city: 'Алматы', people: [
                { cells: [{ code: 'р' }, { code: 'н' }], totals: { worked: 1, missed: 1, late: 5, early: 0 } },
                { cells: [{ code: 'р' }, { code: 'р' }], totals: { worked: 2, missed: 0, late: 0, early: 30 } },
            ] },
            { city: 'Астана', people: [
                { cells: [{ code: 'Б/С' }, { code: 'р' }], totals: { worked: 1, missed: 1, late: 7, early: 0 } },
            ] },
        ],
    };
    assert.deepEqual(timesheetColumnTotals(payload),
        { perDay: [2, 2], worked: 4, missed: 2, late: 12, early: 30, people: 3 });
    assert.deepEqual(timesheetColumnTotals(null), { perDay: [], worked: 0, missed: 0, late: 0, early: 0, people: 0 });
    assert.ok(TIMESHEET_LEGEND.some((line) => line.includes('Б/С')));
});

test('запрос повторяет отборы, пустой отбор — без ограничений', () => {
    assert.equal(timesheetQuery('2026-10', emptyTimesheetFilters()), 'month=2026-10');
    const query = new URLSearchParams(timesheetQuery('2026-10', {
        cities: ['Алматы', TIMESHEET_NO_CITY], userIds: [5, '7'], jobTitles: ['менеджер'],
    }));
    assert.deepEqual(query.getAll('city'), ['Алматы', '__none__']);
    assert.deepEqual(query.getAll('user_id'), ['5', '7']);
    assert.deepEqual(query.getAll('job_title'), ['менеджер']);
});

test('списки отборов из ответа сервера; уволенный в месяце — приглушён', () => {
    const options = timesheetSelectOptions({
        cities: [{ value: 'Алматы', label: 'Алматы' }, { value: '__none__', label: 'Город не указан' }],
        people: [{ id: 5, name: 'Альфа Анна', city: 'Алматы', active: true },
                 { id: 6, name: 'Бета Болат', city: '', active: false }],
        job_titles: ['менеджер'],
    });
    assert.deepEqual(options.people, [
        { value: '5', label: 'Альфа Анна', meta: 'Алматы', muted: false },
        { value: '6', label: 'Бета Болат', meta: undefined, muted: true },
    ]);
    assert.deepEqual(options.jobs, [{ value: 'менеджер', label: 'менеджер' }]);
});

test('смена месяца убирает из отбора то, чего в новом месяце нет', () => {
    const options = timesheetSelectOptions({
        cities: [{ value: 'Астана', label: 'Астана' }],
        people: [{ id: 5, name: 'Альфа Анна', active: true }],
        job_titles: ['менеджер'],
    });
    const before = { cities: ['Алматы', 'Астана'], userIds: ['5', '9'], jobTitles: ['техперсонал'] };
    const after = pruneTimesheetFilters(before, options);
    assert.deepEqual(after, { cities: ['Астана'], userIds: ['5'], jobTitles: [] });
    assert.equal(sameTimesheetFilters(before, after), false);
    assert.equal(sameTimesheetFilters(after, { cities: ['Астана'], userIds: [5], jobTitles: [] }), true);
});

test('имя файла — из ответа сервера, иначе своё', () => {
    const encoded = `attachment; filename*=UTF-8''${encodeURIComponent('Табель фронт-офисы 2026-10.xlsx')}`;
    assert.equal(downloadFileName(encoded, 'x.xlsx'), 'Табель фронт-офисы 2026-10.xlsx');
    assert.equal(downloadFileName('attachment; filename="tabel.xlsx"', 'x.xlsx'), 'tabel.xlsx');
    assert.equal(downloadFileName(null, timesheetFileName(wholeMonthPeriod('2026-10'))), 'Табель фронт-офисы 2026-10.xlsx');
});

test('плитка окна отчёта — только по флагу, значение то же, что у страницы', () => {
    assert.equal(TIMESHEET_OPTION_VALUE, TIMESHEET_SCOPE);
    const plain = buildHoursReportOptions({ group: { name: 'Группа' } });
    assert.deepEqual(plain.map((option) => option.value), ['by_sv', 'all']);
    const withTimesheet = buildHoursReportOptions({ group: { name: 'Группа' }, timesheet: true });
    assert.deepEqual(withTimesheet.map((option) => option.value), ['by_sv', 'all', TIMESHEET_SCOPE]);
    const tile = withTimesheet[2];
    assert.equal(tile.label, 'Табель фронт-офисов');
    assert.equal(tile.disabled, false);
    assert.equal(tile.subtitle, 'Excel, лист «табель»');
    // У главы фронт-офисов — только табель: «По СВ» и «Общий» про часы линии.
    const headOnly = buildHoursReportOptions({ timesheet: true, timesheetOnly: true });
    assert.deepEqual(headOnly.map((option) => option.value), [TIMESHEET_SCOPE]);
    // Без права на табель флаг ничего не убирает.
    assert.deepEqual(buildHoursReportOptions({ timesheetOnly: true }).map((option) => option.value), ['by_sv', 'all']);
});

/* ── Период «с — по» (просьба владельца 19.11.2026: от дня до месяца) ───────
   Правило — двойник rules.period_max_end на сервере: таблица ниже та же, что в
   tests/test_front_office_timesheet_rules.py (PeriodTests). */

test('месяц считается от дня начала — те же даты, что у сервера', () => {
    const cases = {
        '2026-11-01': '2026-11-30',
        '2026-10-01': '2026-10-31',
        '2027-02-01': '2027-02-28',
        '2026-10-15': '2026-11-14',
        '2026-12-15': '2027-01-14',
        '2026-10-31': '2026-11-30',
        '2027-01-31': '2027-02-28',
        '2028-01-30': '2028-02-29',
    };
    for (const [from, to] of Object.entries(cases)) {
        assert.equal(periodMaxEnd(from), to, from);
        // Обратно: самое раннее начало, от которого месяц ещё достаёт до to.
        assert.ok(periodMaxEnd(periodMinStart(to)) >= to, to);
    }
    assert.equal(periodMinStart('2026-11-30'), '2026-10-31');
    assert.equal(periodMinStart('2026-11-14'), '2026-10-15');
    assert.deepEqual(timesheetSpanLimit('2026-11-01'), { min: '2026-10-02', max: '2026-11-30' });
    assert.equal(periodMaxEnd('2026-02-30'), '');
});

test('годный период: обе даты, начало не позже конца, не длиннее месяца', () => {
    assert.equal(isValidTimesheetPeriod({ from: '2026-11-05', to: '2026-11-05' }), true);
    assert.equal(isValidTimesheetPeriod({ from: '2026-11-01', to: '2026-11-30' }), true);
    assert.equal(isValidTimesheetPeriod({ from: '2026-11-01', to: '2026-12-01' }), false);
    assert.equal(isValidTimesheetPeriod({ from: '2026-11-10', to: '2026-11-01' }), false);
    assert.equal(isValidTimesheetPeriod({ from: '2026-11-31', to: '2026-12-01' }), false);
    assert.equal(isValidTimesheetPeriod(null), false);
    assert.equal(timesheetPeriodKey({ from: '2026-11-01', to: '2026-11-15' }), '2026-11-01..2026-11-15');
    assert.equal(timesheetPeriodKey({ from: '2026-11-01', to: '2026-12-15' }), '');
});

test('месяц страницы и целый месяц', () => {
    assert.deepEqual(wholeMonthPeriod('2026-11'), { from: '2026-11-01', to: '2026-11-30' });
    assert.deepEqual(wholeMonthPeriod('2028-02'), { from: '2028-02-01', to: '2028-02-29' });
    assert.equal(wholeMonthPeriod('2026-13'), null);
    assert.equal(wholeMonthOfPeriod({ from: '2026-11-01', to: '2026-11-30' }), '2026-11');
    assert.equal(wholeMonthOfPeriod({ from: '2026-11-01', to: '2026-11-29' }), null);
    assert.equal(periodMonth({ from: '2026-10-25', to: '2026-11-05' }), '2026-10');
});

test('подпись периода и имя файла — как у сервера', () => {
    const cases = [
        [{ from: '2026-10-01', to: '2026-10-31' }, 'октябрь 2026', 'Табель фронт-офисы 2026-10.xlsx'],
        [{ from: '2026-11-05', to: '2026-11-05' }, '5 ноября 2026', 'Табель фронт-офисы 05.11.2026.xlsx'],
        [{ from: '2026-11-01', to: '2026-11-15' }, '1–15 ноября 2026', 'Табель фронт-офисы 01.11.2026 — 15.11.2026.xlsx'],
        [{ from: '2026-10-25', to: '2026-11-05' }, '25 октября – 5 ноября 2026',
            'Табель фронт-офисы 25.10.2026 — 05.11.2026.xlsx'],
        [{ from: '2026-12-25', to: '2027-01-05' }, '25 декабря 2026 – 5 января 2027',
            'Табель фронт-офисы 25.12.2026 — 05.01.2027.xlsx'],
    ];
    for (const [period, title, file] of cases) {
        assert.equal(timesheetPeriodTitle(period), title);
        assert.equal(timesheetFileName(period), file);
    }
});

test('запрос с периодом несёт и месяц его начала — сервер без периода построит месяц', () => {
    const query = new URLSearchParams(timesheetQuery({ from: '2026-10-25', to: '2026-11-05' }, { userIds: [5] }));
    assert.equal(query.get('month'), '2026-10');
    assert.equal(query.get('date_from'), '2026-10-25');
    assert.equal(query.get('date_to'), '2026-11-05');
    assert.deepEqual(query.getAll('user_id'), ['5']);
    assert.equal(timesheetQuery({ from: '2026-11-01', to: '2026-11-30' }, null),
        'month=2026-11&date_from=2026-11-01&date_to=2026-11-30');
});

test('стрелки: месяц листается месяцами, остальное — на свою длину, не длиннее месяца', () => {
    assert.deepEqual(shiftTimesheetPeriod({ from: '2026-11-01', to: '2026-11-30' }, 1), { from: '2026-12-01', to: '2026-12-31' });
    assert.deepEqual(shiftTimesheetPeriod({ from: '2026-12-01', to: '2026-12-31' }, 1), { from: '2027-01-01', to: '2027-01-31' });
    assert.deepEqual(shiftTimesheetPeriod({ from: '2026-03-01', to: '2026-03-31' }, -1), { from: '2026-02-01', to: '2026-02-28' });
    assert.deepEqual(shiftTimesheetPeriod({ from: '2026-11-05', to: '2026-11-05' }, -1), { from: '2026-11-04', to: '2026-11-04' });
    assert.deepEqual(shiftTimesheetPeriod({ from: '2026-11-01', to: '2026-11-01' }, -1), { from: '2026-10-31', to: '2026-10-31' });
    assert.deepEqual(shiftTimesheetPeriod({ from: '2026-11-02', to: '2026-11-08' }, 1), { from: '2026-11-09', to: '2026-11-15' });
    // «Месяц от 15-го» листается месяцами от того же числа и не сжимается после февраля
    // (разбор 19.11.2026: было 15.01–14.02 → … → 18.01–14.02).
    assert.deepEqual(shiftTimesheetPeriod({ from: '2026-10-15', to: '2026-11-14' }, 1), { from: '2026-11-15', to: '2026-12-14' });
    let period = { from: '2026-01-15', to: '2026-02-14' };
    const forward = [];
    for (let i = 0; i < 3; i += 1) { period = shiftTimesheetPeriod(period, 1); forward.push(`${period.from}..${period.to}`); }
    assert.deepEqual(forward, ['2026-02-15..2026-03-14', '2026-03-15..2026-04-14', '2026-04-15..2026-05-14']);
    for (let i = 0; i < 3; i += 1) period = shiftTimesheetPeriod(period, -1);
    assert.deepEqual(period, { from: '2026-01-15', to: '2026-02-14' });
    // Число, которого в соседнем месяце нет, — его последний день.
    assert.deepEqual(shiftTimesheetPeriod({ from: '2027-01-31', to: '2027-02-28' }, 1), { from: '2027-02-28', to: '2027-03-27' });
});

test('подписи периода: кнопка в шапке — коротко, поле окна — как заголовок окна', () => {
    const cases = [
        [{ from: '2026-11-01', to: '2026-11-30' }, 'Ноябрь 2026', 'Ноябрь 2026'],
        [{ from: '2026-05-05', to: '2026-05-05' }, '5 мая 2026', '5 мая 2026'],
        [{ from: '2026-11-03', to: '2026-11-14' }, '3–14 ноя 2026', '3–14 ноября 2026'],
        [{ from: '2026-10-25', to: '2026-11-05' }, '25 окт – 5 ноя 2026', '25 октября – 5 ноября 2026'],
        [{ from: '2026-12-25', to: '2027-01-05' }, '25 дек – 5 янв 2027', '25 декабря 2026 – 5 января 2027'],
    ];
    for (const [period, chip, field] of cases) {
        assert.equal(timesheetPeriodChip(period), chip);
        assert.equal(timesheetPeriodFieldLabel(period), field);
    }
    // Кнопка постоянной ширины — под самую длинную короткую подпись.
    assert.match(TIMESHEET_PERIOD_BUTTON_WIDTH, /^w-\[\d+px\]$/);
});

test('быстрые периоды: «Вчера» и «Этот месяц» на день нажатия', () => {
    const presets = timesheetPeriodPresets('2026-11-01T10:00:00');
    assert.deepEqual(presets.map((preset) => preset.label), ['Вчера', 'Этот месяц']);
    assert.deepEqual(presets[0].range(), { from: '2026-10-31', to: '2026-10-31' });
    assert.deepEqual(presets[1].range(), { from: '2026-11-01', to: '2026-11-30' });
    assert.ok(TIMESHEET_PERIOD_HINT.includes('до месяца'));
});

test('отбор пуст — табель общий', () => {
    assert.equal(hasTimesheetFilters(emptyTimesheetFilters()), false);
    assert.equal(hasTimesheetFilters({ cities: [], userIds: ['5'], jobTitles: [] }), true);
    assert.equal(hasTimesheetFilters(null), false);
});

test('заголовок окна при плитке табеля называет период', () => {
    const options = buildHoursReportOptions({ timesheet: true, timesheetOnly: true, timesheetPeriodTitle: '1–15 ноября 2026' });
    assert.equal(options[0].title, 'Отчёт за 1–15 ноября 2026');
    const plain = buildHoursReportOptions({ group: { name: 'Группа' }, timesheet: true });
    assert.equal(plain[2].title, undefined);
    assert.equal(plain[0].title, undefined);
});

test('«нет карточки» в окне — только про тех, кто попадёт в файл при отборе', () => {
    const payload = {
        people: [
            { id: 1, name: 'Альфа Анна', city: 'Астана', job_title: 'менеджер', active: true, card: true },
            { id: 2, name: 'Бета Болат', city: 'Алматы', job_title: 'техперсонал', active: true, card: false },
            { id: 3, name: 'Вега Вера', city: '', job_title: 'менеджер', active: true, card: false },
            { id: 4, name: 'Гамма Гуля', city: 'Алматы', job_title: 'менеджер', active: false, card: false },
        ],
        without_card: ['Бета Болат', 'Вега Вера'],
    };
    assert.deepEqual(timesheetWithoutCard(payload, emptyTimesheetFilters()), ['Бета Болат', 'Вега Вера']);
    assert.deepEqual(timesheetWithoutCard(payload, { cities: ['алматы'], userIds: [], jobTitles: [] }), ['Бета Болат']);
    assert.deepEqual(timesheetWithoutCard(payload, { cities: [TIMESHEET_NO_CITY], userIds: [], jobTitles: [] }), ['Вега Вера']);
    assert.deepEqual(timesheetWithoutCard(payload, { cities: [], userIds: ['1'], jobTitles: [] }), []);
    assert.deepEqual(timesheetWithoutCard(payload, { cities: [], userIds: [], jobTitles: ['Менеджер'] }), ['Вега Вера']);
    // Порядок — как у сервера (str.casefold): «Ё» после «Я», как над табелем.
    const yo = { people: [
        { id: 7, name: 'Ёлкин Ерлан', city: '', job_title: 'менеджер', active: true, card: false },
        { id: 8, name: 'Жуков Жан', city: '', job_title: 'менеджер', active: true, card: false },
        { id: 9, name: 'Ахметов Арман', city: '', job_title: 'менеджер', active: true, card: false },
    ] };
    assert.deepEqual(timesheetWithoutCard(yo, emptyTimesheetFilters()), ['Ахметов Арман', 'Жуков Жан', 'Ёлкин Ерлан']);
    // Сервер без признака карточки — его готовый список.
    assert.deepEqual(timesheetWithoutCard({ people: [{ id: 1, name: 'А' }], without_card: ['А'] }, null), ['А']);
});
