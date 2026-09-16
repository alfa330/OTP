/* Проверка ИИН — двойник sign_links/iin.py. Те же коды ошибок и те же слова:
 * поле подсказывает ровно то, что ответил бы сервер, и оператор не видит двух
 * разных объяснений одной опечатки.
 *
 * ИИН Казахстана — 12 цифр: ГГММДД (дата рождения), В (век и пол: 1/2 — XIX,
 * 3/4 — XX, 5/6 — XXI; 0 — век не проставлен), NNNN, К (контрольная).
 * Контрольная цифра: сумма первых одиннадцати с весами 1..11 по модулю 11;
 * остаток 10 — второй проход с весами 3..11, 1, 2; снова 10 — такого ИИН нет.
 *
 * Зачем проверять до отправки. Генератор Sapar на опечатку отвечает тем же
 * «Нет документов на подписание», что и на чужой ИИН, — оператор ушёл бы с
 * ответом «документов нет» там, где надо переспросить цифру. Контрольная сумма
 * ловит любую одиночную опечатку и перестановку соседних цифр.
 */

export const IIN_LENGTH = 12;

const WEIGHTS_FIRST = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
const WEIGHTS_SECOND = [3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2];

// Разделители, которые человек ставит между цифрами, переписывая ИИН из
// документа. Снимаются молча; всё остальное — ошибка «только цифры».
const SEPARATORS = /[\s \-.‑‒–—]/g;

export const IIN_ERRORS = {
    empty: 'Введите ИИН',
    digits: 'ИИН состоит только из цифр',
    length: 'В ИИН должно быть 12 цифр',
    date: 'Первые шесть цифр — дата рождения, такой даты не бывает',
    century: 'Седьмая цифра ИИН — век и пол, она бывает от 0 до 6',
    checksum: 'ИИН не сходится по контрольной цифре — проверьте цифры',
};

export const normalizeIin = (raw) => String(raw ?? '').replace(SEPARATORS, '').trim();

const checksum = (firstEleven) => {
    const digits = firstEleven.split('').map(Number);
    let total = digits.reduce((sum, d, i) => sum + d * WEIGHTS_FIRST[i], 0) % 11;
    if (total === 10) {
        total = digits.reduce((sum, d, i) => sum + d * WEIGHTS_SECOND[i], 0) % 11;
        if (total === 10) return null;
    }
    return total;
};

const centuryOf = (digit) => {
    if (digit === '1' || digit === '2') return 1800;
    if (digit === '3' || digit === '4') return 1900;
    if (digit === '5' || digit === '6') return 2000;
    return null;
};

const daysInMonth = (year, month) => new Date(year, month, 0).getDate();

/* Дата из первых шести цифр существует. Век известен — проверяем настоящий
   год; не проставлен — берём високосный, чтобы не отказать 29 февраля
   человеку, чей год мы не знаем. */
const dateIsPlausible = (iin) => {
    const yearTail = Number(iin.slice(0, 2));
    const month = Number(iin.slice(2, 4));
    const day = Number(iin.slice(4, 6));
    if (month < 1 || month > 12) return false;
    const century = centuryOf(iin[6]);
    const year = century === null ? 2000 : century + yearTail;
    return day >= 1 && day <= daysInMonth(year, month);
};

/** { iin, error } — ровно одно из двух заполнено. */
export const validateIin = (raw) => {
    const value = normalizeIin(raw);
    if (!value) return { iin: null, error: 'empty' };
    if (!/^\d+$/.test(value)) return { iin: null, error: 'digits' };
    if (value.length !== IIN_LENGTH) return { iin: null, error: 'length' };
    if (!'0123456'.includes(value[6])) return { iin: null, error: 'century' };
    if (!dateIsPlausible(value)) return { iin: null, error: 'date' };
    const control = checksum(value.slice(0, 11));
    if (control === null || control !== Number(value[11])) return { iin: null, error: 'checksum' };
    return { iin: value, error: null };
};

export const iinErrorMessage = (code) => IIN_ERRORS[code] || IIN_ERRORS.checksum;

/* Показ ИИН: «900101 300 007» — тройки читаются с голоса легче сплошной строки,
   а первые шесть цифр (дата) остаются одним куском. Только для чтения: в поле
   ввода и в запрос уходят голые цифры. */
export const formatIin = (value) => {
    const digits = normalizeIin(value);
    if (digits.length !== IIN_LENGTH) return digits || '—';
    return `${digits.slice(0, 6)} ${digits.slice(6, 9)} ${digits.slice(9)}`;
};
