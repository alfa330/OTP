/* Период выгрузки «Обращений» в Excel — без React, чтобы проверялся тестом
 * (tests/crm_export_period.test.mjs). Те же правила, что у сервера
 * (crm/routes.py::_period): иначе кнопка активна, а сервер отказывает. */

export const EXPORT_MAX_DAYS = 366;

const pad = (value) => String(value).padStart(2, '0');

const iso = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

/* По умолчанию — текущий месяц по сегодня: выгрузку чаще всего просят «за
 * месяц», и начало месяца набирать руками незачем. */
export const defaultPeriod = (now = new Date()) => ({
    from: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-01`,
    to: iso(now),
});

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/* Дни между датами включительно. От полудня — чтобы переход на летнее время
 * в чужом поясе не съел сутки. */
const spanDays = (from, to) => Math.round(
    (new Date(`${to}T12:00:00`) - new Date(`${from}T12:00:00`)) / 86400000) + 1;

export const periodProblem = (from, to) => {
    if (!DAY.test(String(from || '')) || !DAY.test(String(to || ''))) {
        return 'Укажите период: дату начала и дату окончания';
    }
    if (to < from) return 'Дата окончания раньше даты начала';
    if (spanDays(from, to) > EXPORT_MAX_DAYS) return 'Период длиннее года — выгрузите по частям';
    return null;
};

const ru = (value) => {
    const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
    return found ? `${found[3]}.${found[2]}.${found[1]}` : String(value || '');
};

/* Имя файла — как у сервера (crm/report.py::filename): браузер берёт его из
 * ссылки, а не из заголовка ответа, и расходиться им незачем. */
export const exportFileName = (from, to) => `Обращения ${ru(from)}–${ru(to)}.xlsx`;

export const exportQuery = (from, to) => new URLSearchParams({ date_from: from, date_to: to }).toString();
