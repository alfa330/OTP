/*
 * Табель фронт-офисов в «Учете часов» (ТЗ «выгрузка табеля», 06.11.2026).
 *
 * Решение владельца: табель только про отдел «Фронт офисы» («Регионы»),
 * выгружают глава этого отдела и суперадмины. Сервер проверяет то же самое
 * (front_office_timesheet/routes.py), здесь — только что показывать.
 *
 * Правила без React: кому плитка и страница, как собрать запрос, как назвать
 * файл. Проверяются тестом без браузера (tests/front_office_timesheet.test.mjs).
 */

export const TIMESHEET_SCOPE = 'front_office_timesheet';
// Отбор «Город не указан»: пустая строка в запросе неотличима от «без отбора».
export const TIMESHEET_NO_CITY = '__none__';
const FRONT_OFFICE_CODE = 'front_office';

const normalizeRole = (role) => {
    const value = String(role || '').trim().toLowerCase();
    return ['superadmin', 'super-admin', 'super admin'].includes(value) ? 'super_admin' : value;
};

export const isSuperAdminUser = (user) => normalizeRole(user?.role) === 'super_admin';

/* Глава отдела «Фронт офисы»: код ищем и в списке возглавляемых отделов, и в
   одиночном поле — у части профилей приходит только одно из двух. */
export const headsFrontOfficeDepartment = (user) => {
    const codes = [];
    const plural = user?.headed_department_codes ?? user?.headedDepartmentCodes;
    if (Array.isArray(plural)) codes.push(...plural);
    codes.push(user?.headed_department_code ?? user?.headedDepartmentCode);
    return codes.some((code) => String(code || '').trim().toLowerCase() === FRONT_OFFICE_CODE);
};

export const canExportFrontOfficeTimesheet = (user) => (
    isSuperAdminUser(user) || headsFrontOfficeDepartment(user)
);

/* Главе фронт-офисов «Учет часов» открыт ради табеля: часы, бонусы и штрафы
   её людей ей не открывали, поэтому раздел у неё — одна страница табеля. */
export const showsOnlyFrontOfficeTimesheet = (user) => (
    headsFrontOfficeDepartment(user) && !isSuperAdminUser(user)
);

export const emptyTimesheetFilters = () => ({ cities: [], userIds: [], jobTitles: [] });

export function timesheetQuery(month, filters) {
    const params = new URLSearchParams();
    params.append('month', String(month || ''));
    (filters?.cities || []).forEach((city) => params.append('city', String(city)));
    (filters?.userIds || []).forEach((id) => params.append('user_id', String(id)));
    (filters?.jobTitles || []).forEach((title) => params.append('job_title', String(title)));
    return params.toString();
}

/* Ответ /options → строки трёх списков отбора. Уволенный в месяце в списке
   есть (он в табеле месяца), но приглушён. */
export function timesheetSelectOptions(payload) {
    return {
        cities: (payload?.cities || []).map((city) => ({ value: String(city.value), label: String(city.label) })),
        people: (payload?.people || []).map((person) => ({
            value: String(person.id),
            label: String(person.name || ''),
            meta: person.city || undefined,
            muted: person.active === false,
        })),
        jobs: (payload?.job_titles || []).map((title) => ({ value: String(title), label: String(title) })),
    };
}

/* Отбор держит только то, что есть в выбранном месяце: сменили месяц — ушедшие
   люди и города из отбора отпадают, а не уходят в запрос невидимыми. */
export function pruneTimesheetFilters(filters, options) {
    const keep = (values, list) => {
        const allowed = new Set((list || []).map((item) => String(item.value)));
        return (values || []).map(String).filter((value) => allowed.has(value));
    };
    return {
        cities: keep(filters?.cities, options?.cities),
        userIds: keep(filters?.userIds, options?.people),
        jobTitles: keep(filters?.jobTitles, options?.jobs),
    };
}

export function sameTimesheetFilters(left, right) {
    const key = (filters) => JSON.stringify([
        [...(filters?.cities || [])].map(String).sort(),
        [...(filters?.userIds || [])].map(String).sort(),
        [...(filters?.jobTitles || [])].map(String).sort(),
    ]);
    return key(left) === key(right);
}

/* Имя файла из Content-Disposition (filename*=UTF-8''… или filename="…"). */
export function downloadFileName(contentDisposition, fallback) {
    const header = String(contentDisposition || '');
    const encoded = header.match(/filename\*\s*=\s*(?:UTF-8'')?["']?([^;"']+)["']?/i);
    if (encoded?.[1]) {
        try {
            return decodeURIComponent(encoded[1]);
        } catch {
            return encoded[1];
        }
    }
    const plain = header.match(/filename\s*=\s*["']?([^;"']+)["']?/i);
    return plain?.[1] || fallback;
}

export const timesheetFallbackName = (month) => `Табель фронт-офисы ${month}.xlsx`;
