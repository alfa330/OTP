/*
 * Разделы «Прием», «Табель учета рабочего времени» и «АВР» (ТЗ HR «iCore 4»): общие функции без React — подписи, деньги, даты, ячейки табеля.
 */
import axios from 'axios';

export const EMPLOYMENT_OPTIONS = [
    { value: 'gph', label: 'ГПХ' },
    { value: 'of', label: 'ТД' },
    { value: 'smz', label: 'СМЗ' },
];
export const EMPLOYMENT_SHORT = { gph: 'ГПХ', of: 'ТД', smz: 'СМЗ' };
export const DOC_TYPE_OPTIONS = [
    { value: 'id_card', label: 'Удостоверение' },
    { value: 'passport', label: 'Паспорт' },
];
export const SCHEDULE_OPTIONS = [
    { value: '5/2', label: '5/2' },
    { value: '6/1', label: '6/1' },
    { value: '2/2', label: '2/2' },
];
const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь',
    'Ноябрь', 'Декабрь'];

export const pad = (value) => String(value).padStart(2, '0');

export function todayIso() {
    const now = new Date();
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function monthOf(iso) {
    return String(iso || todayIso()).slice(0, 7);
}

export function shiftMonth(month, delta) {
    const [year, mon] = String(month).split('-').map(Number);
    const date = new Date(year, mon - 1 + delta, 1);
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}`;
}

export function monthLabel(month) {
    const [year, mon] = String(month || '').split('-').map(Number);
    if (!year || !mon) return '';
    return `${MONTHS[mon - 1]} ${year}`;
}

export function monthBounds(month) {
    const [year, mon] = String(month).split('-').map(Number);
    const last = new Date(year, mon, 0).getDate();
    return { from: `${year}-${pad(mon)}-01`, to: `${year}-${pad(mon)}-${pad(last)}` };
}

export function dateText(iso) {
    if (!iso) return '';
    const [year, mon, day] = String(iso).slice(0, 10).split('-');
    return day && mon && year ? `${day}.${mon}.${year}` : String(iso);
}

export function moneyText(value) {
    if (value === null || value === undefined || value === '') return '';
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return number.toLocaleString('ru-RU', { maximumFractionDigits: 2 }).replace(/ /g, ' ');
}

export function initials(name) {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '·';
}

export function contractLine(person) {
    const parts = [EMPLOYMENT_SHORT[person?.employment_type] || '', person?.company || ''];
    if (person?.contract_number) parts.push(`№ ${person.contract_number}`);
    return parts.filter(Boolean).join(' · ');
}

/* Ячейка табеля → тон: рабочий день нейтральный (его большинство — красить нечего),
   выходной приглушён, отсутствия — мягкими плашками, как в табеле фронт-офисов. */
export function cellTone(value) {
    if (value === '' || value === null || value === undefined) return 'empty';
    if (value === 'В') return 'off';
    if (value === 'О') return 'leave';
    if (value === 'А') return 'unpaid';
    if (value === 'Б') return 'sick';
    return 'work';
}

/* «1 сотрудник», «2 сотрудника», «5 сотрудников» — forms: [один, два-четыре, пять и больше]. */
export function plural(count, forms) {
    const number = Math.abs(Math.trunc(Number(count) || 0));
    if (number % 10 === 1 && number % 100 !== 11) return forms[0];
    if (number % 10 >= 2 && number % 10 <= 4 && !(number % 100 >= 12 && number % 100 <= 14)) return forms[1];
    return forms[2];
}

const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября',
    'октября', 'ноября', 'декабря'];

/* Заголовок дня в журнале: «Сегодня», «Вчера», «8 ноября», в другом году — «8 ноября 2025».
   Время в записях журнала — по Алматы, без пояса: дату берём из строки, а не через Date. */
export function dayTitle(isoDay, todayIso_ = todayIso()) {
    const [year, month, day] = String(isoDay || '').slice(0, 10).split('-').map(Number);
    if (!year || !month || !day) return '';
    const today = String(todayIso_ || '').slice(0, 10);
    const yesterday = (() => {
        const [ty, tm, td] = today.split('-').map(Number);
        if (!ty) return '';
        const date = new Date(ty, tm - 1, td - 1);
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    })();
    const iso = `${year}-${pad(month)}-${pad(day)}`;
    if (iso === today) return 'Сегодня';
    if (iso === yesterday) return 'Вчера';
    const sameYear = today.slice(0, 4) === String(year);
    return `${day} ${MONTHS_GENITIVE[month - 1]}${sameYear ? '' : ` ${year}`}`;
}

export function timeOf(iso) {
    const time = String(iso || '').split('T')[1] || '';
    return time.slice(0, 5);
}

export function errorText(error, fallback = 'Не получилось — попробуйте ещё раз') {
    const data = error?.response?.data;
    if (data && typeof data === 'object' && data.error) return data.error;
    return error?.message && !/status code/i.test(error.message) ? error.message : fallback;
}

export async function blobErrorText(error, fallback) {
    const data = error?.response?.data;
    if (data instanceof Blob) {
        try {
            const parsed = JSON.parse(await data.text());
            if (parsed?.error) return parsed.error;
        } catch {
            // не JSON — общий текст ниже
        }
    }
    return errorText(error, fallback);
}

function fileNameFrom(disposition, fallback) {
    const header = String(disposition || '');
    const star = header.match(/filename\*=UTF-8''([^;]+)/i);
    if (star) {
        try {
            return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''));
        } catch {
            // ниже — простой вариант
        }
    }
    const plain = header.match(/filename="?([^";]+)"?/i);
    return plain ? plain[1] : fallback;
}

const EXTENSIONS = {
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/zip': '.zip',
};

/* Запасное имя — с расширением по типу ответа: иначе одиночный .docx сохранялся бы как .zip. */
function withExtension(fallback, mime) {
    const ext = EXTENSIONS[String(mime || '').split(';')[0].trim()];
    if (!ext) return fallback;
    return String(fallback || 'файл').replace(/\.(docx|xlsx|zip)$/i, '') + ext;
}

/* Скачивание в этом же окне (как «Сформировать отчёт» «Учета часов»): fetch с токеном →
   blob → <a download>. Подписанные ссылки хранилища здесь не нужны. */
export function saveBlobResponse(response, fallback) {
    const name = fileNameFrom(response.headers?.['content-disposition'], withExtension(fallback, response.data?.type));
    const url = window.URL.createObjectURL(response.data);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
    return name;
}

export function createHrApi(apiBaseUrl, headersRef) {
    const url = (path) => `${apiBaseUrl}/api/hr_office${path}`;
    // Заголовки сливаются: свой Content-Type у загрузки файла не должен стереть авторизацию.
    const config = (extra = {}) => {
        const { headers: own = {}, ...rest } = extra || {};
        return { headers: { ...(headersRef.current ? headersRef.current() : {}), ...own }, ...rest };
    };
    return {
        get: (path, params) => axios.get(url(path), config({ params })),
        post: (path, body, extra) => axios.post(url(path), body ?? {}, config(extra)),
        put: (path, body) => axios.put(url(path), body ?? {}, config()),
        del: (path, params) => axios.delete(url(path), config({ params })),
        upload: (path, file, fields = {}) => {
            const form = new FormData();
            form.append('file', file);
            Object.entries(fields || {}).forEach(([key, value]) => {
                if (value !== undefined && value !== null) form.append(key, value);
            });
            return axios.post(url(path), form, config());
        },
        download: async (method, path, { params, body, fallback } = {}) => {
            const response = await axios({ method, url: url(path), params, data: body, responseType: 'blob', ...config() });
            return saveBlobResponse(response, fallback || 'файл');
        },
    };
}
