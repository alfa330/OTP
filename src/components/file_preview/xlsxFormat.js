/* Значение ячейки Excel → строка, как её показал бы Excel с русскими настройками.

   Главное — даты: в файле дата хранится числом дней (45231), и без формата
   ячейки человек увидел бы это число вместо «01.11.2023». Поэтому формат
   ячейки читается всегда, а не только когда «похоже на дату». */

export const BUILTIN_FORMATS = {
    0: 'General', 1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00', 9: '0%', 10: '0.00%', 11: '0.00E+00',
    12: '# ?/?', 13: '# ??/??', 14: 'dd.mm.yyyy', 15: 'd-mmm-yy', 16: 'd-mmm', 17: 'mmm-yy',
    18: 'h:mm AM/PM', 19: 'h:mm:ss AM/PM', 20: 'h:mm', 21: 'h:mm:ss', 22: 'dd.mm.yyyy h:mm',
    37: '#,##0 ;(#,##0)', 38: '#,##0 ;[Red](#,##0)', 39: '#,##0.00;(#,##0.00)', 40: '#,##0.00;[Red](#,##0.00)',
    45: 'mm:ss', 46: '[h]:mm:ss', 47: 'mmss.0', 48: '##0.0E+0', 49: '@',
};
// Номера 27–36 и 50–58 — даты в восточноазиатских локалях; для нас — просто дата.
for (let id = 27; id <= 36; id += 1) BUILTIN_FORMATS[id] = 'dd.mm.yyyy';
for (let id = 50; id <= 58; id += 1) BUILTIN_FORMATS[id] = 'dd.mm.yyyy';

const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

const firstSection = (code) => {
    // ';' внутри кавычек — не разделитель секций
    let quoted = false;
    for (let index = 0; index < code.length; index += 1) {
        if (code[index] === '"') quoted = !quoted;
        if (code[index] === ';' && !quoted) return code.slice(0, index);
    }
    return code;
};

/** Формат без надписей в кавычках, экранированных знаков, цвета и локали. */
const stripLiterals = (section) => section
    .replace(/"[^"]*"/g, '')
    .replace(/\\./g, '')
    .replace(/\[(?!h+\]|m+\]|s+\])[^\]]*\]/gi, '')
    .replace(/[_*]./g, '');

export function isDateFormat(code) {
    if (!code) return false;
    const clean = stripLiterals(firstSection(String(code)));
    if (/^\s*general\s*$/i.test(clean) || clean.trim() === '@') return false;
    const withoutFraction = clean.replace(/s+\.0+/gi, 's');
    return /[dmyhs]/i.test(withoutFraction) && !/[0#?]/.test(withoutFraction);
}

/** Число дней Excel → части даты (UTC, без часового пояса машины). */
export function excelSerialToParts(serial, date1904 = false) {
    let days = Math.floor(serial);
    let seconds = Math.round((serial - days) * 86400);
    if (seconds >= 86400) {
        days += 1;
        seconds -= 86400;
    }
    if (date1904) days += 1462;
    // В системе 1900 Excel считает 1900 год високосным: день 60 — несуществующее 29.02.1900.
    else if (days < 60) days += 1;
    const date = new Date(Date.UTC(1899, 11, 30) + days * 86400000 + seconds * 1000);
    return {
        year: date.getUTCFullYear(),
        month: date.getUTCMonth() + 1,
        day: date.getUTCDate(),
        hours: date.getUTCHours(),
        minutes: date.getUTCMinutes(),
        seconds: date.getUTCSeconds(),
        totalSeconds: Math.round(serial * 86400),
    };
}

const pad = (value) => String(value).padStart(2, '0');

export function formatDateValue(serial, code, date1904 = false) {
    const parts = excelSerialToParts(serial, date1904);
    const clean = stripLiterals(firstSection(String(code))).toLowerCase().replace(/am\/pm|a\/p/g, '');
    const hasHours = /h/.test(clean);
    const hasSeconds = /s/.test(clean);
    // Дата — часть до часов; «mm:ss» без часов — только время.
    const datePart = hasHours ? clean.slice(0, clean.indexOf('h')).replace(/\[$/, '') : (hasSeconds ? '' : clean);
    const hasDay = /d/.test(datePart);
    const hasMonth = /m/.test(datePart);
    const hasYear = /y/.test(datePart);
    let date = '';
    if (hasDay && hasMonth && hasYear) date = `${pad(parts.day)}.${pad(parts.month)}.${parts.year}`;
    else if (hasDay && hasMonth) date = `${pad(parts.day)}.${pad(parts.month)}`;
    else if (hasMonth && hasYear) {
        date = /mmm/.test(datePart)
            ? `${(/mmmm/.test(datePart) ? MONTHS : MONTHS_SHORT)[parts.month - 1]} ${parts.year}`
            : `${pad(parts.month)}.${parts.year}`;
    } else if (hasMonth) date = (/mmmm/.test(datePart) ? MONTHS : MONTHS_SHORT)[parts.month - 1];
    else if (hasYear) date = String(parts.year);
    else if (hasDay) date = String(parts.day);
    let time = '';
    if (/\[h+\]/.test(clean)) {
        const total = Math.abs(parts.totalSeconds);
        time = `${serial < 0 ? '-' : ''}${Math.floor(total / 3600)}:${pad(Math.floor(total / 60) % 60)}${hasSeconds ? `:${pad(total % 60)}` : ''}`;
    } else if (hasHours) {
        time = `${pad(parts.hours)}:${pad(parts.minutes)}${hasSeconds ? `:${pad(parts.seconds)}` : ''}`;
    } else if (hasSeconds) {
        time = `${pad(parts.minutes)}:${pad(parts.seconds)}`;
    }
    return [date, time].filter(Boolean).join(' ') || `${pad(parts.day)}.${pad(parts.month)}.${parts.year}`;
}

/** Надписи формата до первой цифры и после последней: «₸», «р.», «%». */
function literals(section) {
    let before = '';
    let after = '';
    let digits = false;
    const add = (text) => {
        if (digits) after += text;
        else before += text;
    };
    for (let index = 0; index < section.length;) {
        const char = section[index];
        if (char === '"') {
            const end = section.indexOf('"', index + 1);
            add(section.slice(index + 1, end < 0 ? undefined : end));
            index = end < 0 ? section.length : end + 1;
        } else if (char === '\\') {
            add(section[index + 1] || '');
            index += 2;
        } else if (char === '[') {
            const end = section.indexOf(']', index);
            const currency = /^\$([^-\]]*)/.exec(section.slice(index + 1, end < 0 ? undefined : end));
            if (currency) add(currency[1]);
            index = end < 0 ? section.length : end + 1;
        } else if (char === '_' || char === '*') {
            index += 2;
        } else if ('0#?'.includes(char)) {
            digits = true;
            index += 1;
        } else if (char === '.' || char === ',') {
            index += 1;
        } else if ((char === 'E' || char === 'e') && /[+-]/.test(section[index + 1] || '')) {
            index += 2;
        } else {
            add(char);
            index += 1;
        }
    }
    return { before, after };
}

const numberFormatters = new Map();
const numberFormatter = (min, max, grouping) => {
    const key = `${min}:${max}:${grouping}`;
    if (!numberFormatters.has(key)) {
        numberFormatters.set(key, new Intl.NumberFormat('ru-RU', {
            minimumFractionDigits: min, maximumFractionDigits: max, useGrouping: grouping,
        }));
    }
    return numberFormatters.get(key);
};

export function formatGeneral(value) {
    if (!Number.isFinite(value)) return String(value);
    if (Math.abs(value) >= 1e15 || (value !== 0 && Math.abs(value) < 1e-9)) {
        return value.toExponential(5).replace('.', ',');
    }
    return numberFormatter(0, 10, false).format(Number(value.toPrecision(15)));
}

export function formatNumber(value, code) {
    if (!code || /^general$/i.test(code.trim())) return formatGeneral(value);
    const section = firstSection(code);
    const clean = stripLiterals(section);
    if (!/[0#?]/.test(clean)) return formatGeneral(value);
    const decimalsMatch = /\.([0#?]+)/.exec(clean);
    const min = decimalsMatch ? (decimalsMatch[1].match(/0/g) || []).length : 0;
    const max = decimalsMatch ? decimalsMatch[1].length : 0;
    if (/E[+-]/i.test(clean)) return value.toExponential(max).replace('.', ',');
    const percent = clean.includes('%');
    const grouping = /[0#?],[0#?]/.test(clean);
    const shown = numberFormatter(min, max, grouping).format(Math.abs(percent ? value * 100 : value));
    const { before, after } = literals(section);
    return `${value < 0 ? '-' : ''}${before}${shown}${after}`.replace(/\s+$/, '');
}

/** Отображение значения ячейки по её типу и формату. */
export function formatCell(raw, type, code, date1904 = false) {
    if (raw === undefined || raw === null || raw === '') return '';
    if (type === 'b') return raw === '1' || raw === 'true' ? 'ИСТИНА' : 'ЛОЖЬ';
    if (type === 'e' || type === 's' || type === 'str' || type === 'inlineStr') return String(raw);
    if (type === 'd') {
        const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(String(raw));
        if (!match) return String(raw);
        return `${match[3]}.${match[2]}.${match[1]}${match[4] && `${match[4]}:${match[5]}` !== '00:00' ? ` ${match[4]}:${match[5]}` : ''}`;
    }
    const value = Number(raw);
    if (!Number.isFinite(value)) return String(raw);
    if (code === '@') return String(raw);
    if (isDateFormat(code)) return formatDateValue(value, code, date1904);
    return formatNumber(value, code || 'General');
}

/** 'A' → 0, 'AB' → 27. */
export function columnIndex(letters) {
    let value = 0;
    for (const char of letters) value = value * 26 + (char.charCodeAt(0) - 64);
    return value - 1;
}

/** 0 → 'A', 27 → 'AB'. */
export function columnName(index) {
    let value = index + 1;
    let out = '';
    while (value > 0) {
        const rest = (value - 1) % 26;
        out = String.fromCharCode(65 + rest) + out;
        value = Math.floor((value - 1) / 26);
    }
    return out;
}
