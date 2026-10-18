/* Поиск «Чатов ОП» по тексту сообщений — правила клиента (сервер:
 * wazzup/chat_search.py). Правила совпадают с серверными намеренно: запрос,
 * который сервер всё равно отклонит, не уходит вовсе, а ключ кэша — тот же
 * текст, что ищет база. */

const MAX_QUERY_LENGTH = 200;
// Три буквы или цифры подряд — меньше индекс триграмм не сузит, и сервер такой
// запрос по тексту не ищет (имя и номер список ищет с первого символа).
const TRIGRAM_WORD = /[\p{L}\p{N}]{3}/u;

export const normalizeSearch = (value) => String(value ?? '').trim().split(/\s+/u).join(' ')
    .slice(0, MAX_QUERY_LENGTH);

export const searchableText = (value) => {
    const query = normalizeSearch(value);
    return TRIGRAM_WORD.test(query) ? query : '';
};

const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/* Текст строки «Сообщения», разбитый на куски для подсветки найденного:
   [{ text, match }]. Регистр — по правилам Unicode (флаги iu), а позиции —
   в исходной строке: toLowerCase меняет длину у части букв, и индексы,
   найденные в копии в нижнем регистре, съезжали бы. */
export function highlightParts(text, query) {
    const source = String(text ?? '');
    const needle = normalizeSearch(query);
    if (!needle || !source) return source ? [{ text: source, match: false }] : [];
    const parts = [];
    let last = 0;
    for (const found of source.matchAll(new RegExp(escapeRegExp(needle), 'giu'))) {
        if (found.index > last) parts.push({ text: source.slice(last, found.index), match: false });
        parts.push({ text: found[0], match: true });
        last = found.index + found[0].length;
    }
    if (last < source.length) parts.push({ text: source.slice(last), match: false });
    return parts;
}

/* Первые страницы недавних запросов. Стирание буквы и возврат к прежнему
   запросу показывают найденное сразу, без запроса; устаревшее (новые
   сообщения могли прийти) показывается и тихо перечитывается. */
export function createSearchCache({ limit = 20, freshMs = 30000, maxAgeMs = 5 * 60000 } = {}) {
    const entries = new Map();
    return {
        get(key, now = Date.now()) {
            const entry = entries.get(key);
            if (!entry) return null;
            if (now - entry.at > maxAgeMs) { entries.delete(key); return null; }
            entries.delete(key);
            entries.set(key, entry);
            return { ...entry.value, stale: now - entry.at > freshMs };
        },
        set(key, value, now = Date.now()) {
            entries.delete(key);
            entries.set(key, { value, at: now });
            while (entries.size > limit) entries.delete(entries.keys().next().value);
        },
        clear() { entries.clear(); },
    };
}
