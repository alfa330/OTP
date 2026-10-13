/* Номера из поля «Телефон» офиса.
 *
 * Поле — свободный текст: маски в форме нет (OfficeEditor.jsx), сервер только
 * обрезает его до 64 знаков, а перенос из статьи (scripts/migrate_wiki_offices.py)
 * оставлял ячейку как есть, если в ней был не один номер. Поэтому в справочнике
 * законно лежат «+7 707 705 08 80, +7 727 333 22 11» и «8 (7212) 50-60-70 доб. 12».
 *
 * Ссылку прежде строили как tel:${phone.replace(/[^\d+]/g, '')}: разделители долой,
 * и два номера склеивались в один несуществующий (+7707705088077273332211), а
 * добавочный прирастал к номеру хвостом (8721250607012). Такой номер набирался с
 * телефона в портале и копировался во вкладке iCORE Phone с тостом «скопирован».
 *
 * Здесь текст режется на номера. У каждого — набираемая часть (цифры и плюс в
 * начале) и добавочный отдельно: его не набирают, а называют.
 *
 * Без React и без DOM — модуль читают тесты через node (tests/wiki_office_phones.test.mjs).
 */

// Между номерами: запятая, точка с запятой, косая черта, перевод строки, «или».
// Скобки — одна группа: split() возвращает и разделители, текст не теряется.
const SEPARATOR = /(\s*[,;/\n]\s*|\s+или\s+)/i;

// Добавочный: «доб. 12», «доп 12», «вн. 205», «внутр. 3», «ext. 5», «#12». Граница
// слова — классом символов, а не \b и не просмотром назад: \b в JS кириллицы не
// видит, а просмотр назад Safari старше 16.4 не разбирает вовсе — модуль, а с ним
// и вкладка «Офисы» портала на таком телефоне не загрузились бы.
const EXTENSION = /(^|[^a-zа-яё])(?:доб|доп|внутр|вн|ext)\.?\s*[:№]?\s*(\d{1,6})|#\s*(\d{1,6})/i;

// Кусок номера: цифры вперемешку со скобками, пробелами, точками и тире; плюс —
// только впереди.
const NUMBER_RUN = /\+?\(?\d[\d\s().\-–—]*\d/g;

// Короче — не телефон («офис 204», номер дома); длиннее E.164 — склейка
// нескольких номеров без разделителя, набирать её нельзя.
const MIN_DIGITS = 5;
const MAX_DIGITS = 15;

const digitsOf = (text) => String(text || '').replace(/\D/g, '');

/* Один кусок между разделителями → { number, ext }. Номер — самый длинный пробег
   цифр в куске: «Офис 2: +7 701 123 45 67» — это +77011234567, а не 2 и номер
   слитно. Добавочный и всё после него в набираемую часть не идут. */
const readChunk = (chunk) => {
    const found = EXTENSION.exec(chunk);
    const ext = found ? (found[2] || found[3] || '') : '';
    const dial = found ? chunk.slice(0, found.index + (found[1] || '').length) : chunk;
    let best = '';
    (dial.match(NUMBER_RUN) || []).forEach((run) => {
        if (digitsOf(run).length > digitsOf(best).length) best = run;
    });
    const digits = digitsOf(best);
    if (digits.length < MIN_DIGITS || digits.length > MAX_DIGITS) return { number: '', ext };
    return { number: best.trimStart().startsWith('+') ? `+${digits}` : digits, ext };
};

/**
 * Текст поля по кускам — для показа ссылками. Кусок с номером:
 * { text, number, ext }, остальное (разделители и то, что номером не читается) —
 * { text }. Склеенные куски дают ровно исходный текст (без крайних пробелов):
 * человек видит то же, что записано в справочнике.
 */
export const phoneSegments = (value) => {
    const text = String(value ?? '').trim();
    if (!text) return [];
    const segments = [];
    text.split(SEPARATOR).forEach((part, index) => {
        if (!part) return;
        if (index % 2) {
            segments.push({ text: part });
            return;
        }
        const { number, ext } = readChunk(part);
        if (number) {
            segments.push({ text: part, number, ext });
            return;
        }
        // «…50-60-70, доб. 12»: добавочный отдельным куском принадлежит номеру перед
        // ним — и в ссылку, и в подсказку при копировании.
        const separator = segments[segments.length - 1];
        const previous = segments[segments.length - 2];
        if (ext && previous?.number && !previous.ext && separator && !separator.number) {
            previous.text += separator.text + part;
            previous.ext = ext;
            segments.pop();
            return;
        }
        segments.push({ text: part });
    });
    return segments;
};

/** Только номера: [{ text, number, ext }] в порядке записи. */
export const officePhones = (value) => phoneSegments(value).filter((part) => part.number);
