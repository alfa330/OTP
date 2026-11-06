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
    showsOnlyFrontOfficeTimesheet,
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

test('у главы фронт-офисов в «Учете часов» одна страница табеля, у суперадмина — весь раздел', () => {
    assert.equal(showsOnlyFrontOfficeTimesheet(frontOfficeHead), true);
    assert.equal(showsOnlyFrontOfficeTimesheet(superAdmin), false);
    assert.equal(showsOnlyFrontOfficeTimesheet({ ...superAdmin, headed_department_codes: ['front_office'] }), false);
    assert.equal(showsOnlyFrontOfficeTimesheet(opHead), false);
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
});
