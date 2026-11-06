// Табель фронт-офисов в «Учете часов»: кому плитка и страница, запрос, имя файла.
// Решение владельца 06.11.2026: выгружают глава фронт-офисов и суперадмины.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    TIMESHEET_NO_CITY,
    TIMESHEET_SCOPE,
    canExportFrontOfficeTimesheet,
    downloadFileName,
    emptyTimesheetFilters,
    headsFrontOfficeDepartment,
    pruneTimesheetFilters,
    sameTimesheetFilters,
    TIMESHEET_LEGEND,
    timesheetAvailable,
    timesheetCellTitle,
    timesheetCellView,
    timesheetColumnTotals,
    timesheetFallbackName,
    timesheetQuery,
    timesheetSelectOptions,
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
    assert.equal(downloadFileName(null, timesheetFallbackName('2026-10')), 'Табель фронт-офисы 2026-10.xlsx');
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
