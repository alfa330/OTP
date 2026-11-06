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

/* Табель на экране «Учета часов» (просьба владельца 06.11.2026: «при выборе группы
   регионов выводилось на сайте тоже, в стилистике сайта»). Режим доступен, когда
   выбрана группа отдела фронт-офисов, а у главы фронт-офисов — и без группы: её
   раздел и есть этот отдел (сервер «Учета часов» режет главу её отделом).
   departmentId — id отдела фронт-офисов с сервера (/api/front_office_timesheet/scope). */
export function timesheetAvailable({ canExport, departmentId, selectedGroup, selectedGroupId, headedDepartmentId }) {
    if (!canExport || departmentId == null) return false;
    const target = Number(departmentId);
    if (selectedGroupId) {
        const groupDepartment = selectedGroup?.department_id ?? selectedGroup?.departmentId;
        return groupDepartment != null && Number(groupDepartment) === target;
    }
    return headedDepartmentId != null && Number(headedDepartmentId) === target;
}

const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
    'августа', 'сентября', 'октября', 'ноября', 'декабря'];

/* Как показать ячейку дня. tone — смысл для цвета: рабочий день нейтральный
   (его большинство, красить нечего), выходной приглушён, отсутствия — мягкими
   плашками. */
export function timesheetCellView(cell) {
    const code = String(cell?.code || '');
    const tone = {
        'р': 'work', 'в': 'off', 'Б/С': 'bs', 'О': 'vacation', 'Б': 'sick', 'н': 'absent',
    }[code] || 'empty';
    return {
        label: code,
        tone,
        incomplete: Boolean(cell?.incomplete) && code === 'р',
        late: Number(cell?.late || 0),
        early: Number(cell?.early || 0),
    };
}

const CODE_TITLES = {
    'р': 'рабочий день',
    'в': 'выходной',
    'Б/С': 'без содержания',
    'О': 'отпуск',
    'Б': 'больничный',
    'н': 'смена без отметок',
};

/* Подсказка ячейки: дата, что за день и откуда числа. */
export function timesheetCellTitle(day, cell) {
    const parts = [];
    const date = String(day?.date || '');
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
    parts.push(match ? `${Number(match[3])} ${MONTHS_GENITIVE[Number(match[2]) - 1]}, ${day?.weekday || ''}`.trim() : date);
    const code = String(cell?.code || '');
    if (code) parts.push(CODE_TITLES[code] || code);
    else if (day?.future) parts.push('ещё не наступил');
    if (code === 'р') {
        const span = [cell?.arrive, cell?.leave].map((value) => value || '—').join('–');
        parts.push(`отметки ${span}`);
        if (cell?.incomplete) parts.push(cell?.arrive ? 'нет отметки ухода' : 'нет отметки прихода');
    }
    if (Number(cell?.late) > 0) parts.push(`опоздание ${cell.late} мин`);
    if (Number(cell?.early) > 0) parts.push(`ранний уход ${cell.early} мин`);
    if (day?.holiday) parts.push('праздник');
    return parts.join(' · ');
}

/* Итоги под таблицей: сколько человек отработали каждый день и суммы колонок. */
export function timesheetColumnTotals(payload) {
    const days = Array.isArray(payload?.days) ? payload.days : [];
    const perDay = days.map(() => 0);
    const sums = { worked: 0, missed: 0, late: 0, early: 0, people: 0 };
    for (const city of payload?.cities || []) {
        for (const person of city?.people || []) {
            sums.people += 1;
            (person?.cells || []).forEach((cell, index) => {
                if (cell?.code === 'р' && index < perDay.length) perDay[index] += 1;
            });
            for (const key of ['worked', 'missed', 'late', 'early']) {
                sums[key] += Number(person?.totals?.[key] || 0);
            }
        }
    }
    return { perDay, ...sums };
}

export const TIMESHEET_LEGEND = [
    'р — рабочий день по отметкам «Отметок»; точка — нет отметки прихода или ухода.',
    'в — выходной, Б/С — без содержания, О — отпуск, Б — больничный, н — смена без отметок.',
    'Красное число — опоздание, мин; оранжевое — ранний уход, мин. Уход, после которого человек снова вошёл, ранним уходом не считается.',
    'Файл — «Сформировать отчёт» → «Табель фронт-офисов».',
];

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
