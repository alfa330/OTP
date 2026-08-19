/* Номера парка: плоский список, где у каждого номера своё место — офис из
 * справочника или «онлайн» (парк без адреса, принимают только по телефону) — и
 * необязательная записка вроде «звонить после 10».
 *
 * Плоско, а не «место → его номера», потому что так их и заводят: сначала
 * номер, потом ему выбирают офис. Два номера одного офиса — обычное дело.
 *
 * Отдельный модуль, а не часть ParkEditor: правила «что можно сохранить» и
 * преобразование формы в тело запроса нужны и форме, и списку парков, и тесту
 * (tests/wiki_park_points.test.mjs) — а тест не поднимет JSX.
 */

/* Ключ строки нужен React'у, а id у новой строки ещё нет: индекс массива в
   роли ключа перепутал бы поля при удалении строки посередине. */
let sequence = 0;
const nextKey = () => { sequence += 1; return `number-${sequence}`; };

/* Все номера казахстанские: код страны +7 задан жёстко, человек набирает
   только десять цифр после него. Так номер нельзя записать «8 707…» в одной
   строке и «+7 707…» в другой — а в старом справочнике было и то и другое. */
export const PHONE_DIGITS = 10;

export const digitsOf = (phone) => {
    const digits = String(phone || '').replace(/\D/g, '');
    // «+7 707…», «8 707…» и «707…» — одно и то же: код страны отбрасываем.
    const local = digits.length > PHONE_DIGITS && /^[78]/.test(digits)
        ? digits.slice(digits.length - PHONE_DIGITS)
        : digits;
    return local.slice(0, PHONE_DIGITS);
};

/* 707 705 08 80 — как номер диктуют вслух. */
export const formatDigits = (digits) => {
    const parts = [digits.slice(0, 3), digits.slice(3, 6), digits.slice(6, 8), digits.slice(8, 10)];
    return parts.filter(Boolean).join(' ');
};

export const toPhone = (digits) => (digits ? `+7 ${formatDigits(digits)}` : '');

export const isOnline = (number) => number?.office_id === null;

export const emptyNumber = (officeId = null) => ({
    key: nextKey(), office_id: officeId, phone: '', note: '',
});

/* Номера для формы: сперва те, что без офиса, за ними офисные — в порядке,
   который отдал сервер (город, позиция). Пустой парк открывается одной пустой
   строкой: номер обязателен, и форма должна показать это полем, а не пустотой. */
export const numbersFromPark = (park) => {
    const rows = [];
    const push = (officeId, list) => (list || []).forEach((item) => rows.push({
        key: nextKey(),
        office_id: officeId,
        phone: typeof item === 'string' ? item : (item?.phone || ''),
        note: (typeof item === 'string' ? '' : (item?.note || '')),
    }));

    push(null, park.phones);
    (park.offices || []).forEach((link) => push(link.office_id, link.phones));
    return rows.length ? rows : [emptyNumber()];
};

/* Тело запроса: тот же плоский список, но нормализованный — номер в единой
   форме «+7 …», пустые строки выброшены, повторы одного номера в одном месте
   сняты (на сервере они всё равно схлопнутся, и форма не должна обещать
   другого). */
export const numbersPayload = (numbers) => {
    const seen = new Set();
    const result = [];
    (numbers || []).forEach((number) => {
        const digits = digitsOf(number.phone);
        if (digits.length !== PHONE_DIGITS) return;
        const key = `${number.office_id ?? 'online'}:${digits}`;
        if (seen.has(key)) return;
        seen.add(key);
        result.push({
            office_id: number.office_id ?? null,
            phone: toPhone(digits),
            note: (number.note || '').trim() || null,
        });
    });
    return result;
};

/* Что мешает сохранить. Строкой, а не булевым: кнопка гаснет, и без причины
   рядом это выглядит поломкой формы. */
export const parkDraftIssue = (draft) => {
    if (!draft) return null;
    if (!draft.name?.trim()) return 'Укажите название парка';

    const numbers = draft.numbers || [];
    const filled = numbers.filter((number) => digitsOf(number.phone).length > 0);
    // Парк без единого номера — справочник, по которому не позвонить.
    if (filled.length === 0) return 'Нужен хотя бы один номер';
    if (filled.some((number) => digitsOf(number.phone).length !== PHONE_DIGITS)) {
        return 'После +7 нужно десять цифр';
    }
    return null;
};
