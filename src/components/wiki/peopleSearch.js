/* Поиск по списку людей и разбивка на страницы — чистая часть.
 *
 * Решение владельца 06.10.2026 про список «кому открыта статья»: «с пагинацией,
 * и поиск как в вики, который учитывает ошибки». Список приходит целиком одним
 * ответом (wiki/article_access.py), людей в нём сотни, а не тысячи, — поэтому
 * и поиск, и страницы считаются здесь, без запроса на каждую букву.
 *
 * «Как в вики» — буквально теми же послаблениями, что у поиска статей
 * (wiki/search.py и wiki/text.py):
 *   • регистр, ё/е и казахские буквы не различаются («Қазына» = «казына»);
 *   • забытая раскладка и латиница чинятся («bdfyjd», «ivanov» → «иванов»);
 *   • опечатка прощается сходством по триграммам — та же мера, что
 *     word_similarity из pg_trgm, и тот же порог 0,45.
 * Похожее идёт в ход, только когда точно не нашлось ничего: набравший
 * фамилию без ошибок не должен искать её среди «почти таких же».
 */
import { foldKazakh, normalizeText } from './searchText.js';
import { structureNeedles } from './structureTree.js';

/* wiki/search.py: _TRIGRAM_THRESHOLD. Ниже порога «алия» находила бы «Алину»
   и «Алису» разом, выше — «иванв» переставал находить «Иванов». */
export const SIMILARITY_THRESHOLD = 0.45;

/** Текст в том виде, в каком его сравнивает поиск. */
export const foldText = (text) => foldKazakh(normalizeText(text));

/** Триграммы слова, как их режет pg_trgm: два пробела перед словом, один после. */
export function trigrams(word) {
    const padded = `  ${word} `;
    const out = [];
    for (let i = 0; i + 3 <= padded.length; i += 1) out.push(padded.slice(i, i + 3));
    return out;
}

/**
 * Сходство запроса со словом — word_similarity из pg_trgm.
 *
 * Наибольшее сходство между триграммами запроса и ЛЮБЫМ непрерывным отрезком
 * триграмм слова. Отрезком, а не всем словом: так «абдр» находит
 * «Абдрахманова» (начало слова совпало целиком), а не штрафуется за его хвост.
 */
export function wordSimilarity(needle, word) {
    const wanted = new Set(trigrams(needle));
    const ordered = trigrams(word);
    let best = 0;
    for (let from = 0; from < ordered.length; from += 1) {
        // Отрезок, начатый с чужой триграммы, заведомо хуже того же без неё.
        if (!wanted.has(ordered[from])) continue;
        const seen = new Set();
        let common = 0;
        for (let to = from; to < ordered.length; to += 1) {
            const gram = ordered[to];
            if (!seen.has(gram)) {
                seen.add(gram);
                if (wanted.has(gram)) common += 1;
            }
            const score = common / (wanted.size + seen.size - common);
            if (score > best) best = score;
        }
    }
    return best;
}

/* Насколько слово запроса нашлось среди слов строки: 1 — слово целиком, 0,95 —
   началом слова, 0,9 — внутри слова, ниже — похожим написанием (само
   сходство); 0 — не нашлось. Слово целиком выше начала: по «оп» сотрудники
   отдела ОП обязаны стоять раньше всех, у кого в строке «оператор».

   Короткие слова ищутся строже. Внутри слова — от трёх букв: по двум совпала
   бы половина списка. Похожее — от четырёх: у трёх букв сходство по триграммам
   вырождается в «те же две первые буквы», и «чта» находила бы всех, у кого в
   строке «Чтение», вместо честного «никого не нашлось». */
const EXACT = 1;
const PREFIX = 0.95;
const INSIDE = 0.9;
const tokenScore = (token, words, fuzzy) => {
    let best = 0;
    for (const word of words) {
        let score = 0;
        if (word === token) score = EXACT;
        else if (word.startsWith(token)) score = PREFIX;
        else if (token.length >= 3 && word.includes(token)) score = INSIDE;
        else if (fuzzy && token.length >= 4) {
            const similar = wordSimilarity(token, word);
            if (similar >= SIMILARITY_THRESHOLD) score = similar;
        }
        if (score > best) best = score;
        if (best === EXACT) break;
    }
    return best;
};

/**
 * Насколько строка отвечает запросу: от 0 (не отвечает) до 1.
 *
 * needles — варианты написания запроса (structureNeedles). Вариант подходит,
 * когда нашлось КАЖДОЕ его слово, в любом порядке: «ерлан абдр» и «абдр ерлан»
 * — один и тот же человек. Оценка варианта — худшее из его слов, оценка
 * строки — лучший из вариантов. fuzzy разрешает похожее написание.
 */
export function matchScore(needles, words, fuzzy = false) {
    let best = 0;
    for (const needle of needles) {
        const tokens = needle.split(/[\s-]+/).filter(Boolean);
        if (!tokens.length) continue;
        let worst = EXACT;
        for (const token of tokens) {
            const score = tokenScore(token, words, fuzzy);
            if (score < worst) worst = score;
            if (!worst) break;
        }
        if (worst > best) best = worst;
    }
    return best;
}

/* Отбор и порядок. Имя раньше остальных полей: по слову «марк» первым должен
   стоять Марк, а не весь отдел маркетинга. Внутри — лучшее совпадение первым,
   равные остаются в прежнем порядке (по алфавиту). */
const pick = (list, needles, fuzzy) => list
    .map((row, index) => {
        const byName = matchScore(needles, row.nameWords, fuzzy);
        const score = byName || matchScore(needles, row.words, fuzzy);
        return { row, index, score, tier: byName ? 0 : 1 };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => a.tier - b.tier || b.score - a.score || a.index - b.index)
    .map((item) => item.row);

/**
 * Строки, отвечающие запросу.
 *
 * Сперва ищем как написано — началом слова или внутри него. Похожее написание
 * идёт в ход, только когда так не нашлось НИЧЕГО: набравший фамилию без ошибок
 * получает её, а не её вместе со всеми «почти такими же»; опечатавшийся —
 * ближайших, лучшие первыми.
 *
 * У строки должны быть nameWords (слова имени) и words (все слова, по которым
 * её ищут) — уже свёрнутые foldText. Пустой запрос возвращает список как есть.
 */
export function searchPeople(rows, query) {
    const list = rows || [];
    const needles = structureNeedles(query);
    if (!needles.length) return list;
    const exact = pick(list, needles, false);
    return exact.length ? exact : pick(list, needles, true);
}

/** Слова, по которым ищут: текст сворачивается и режется по пробелам и дефисам. */
export const searchWords = (...parts) => (
    foldText(parts.filter(Boolean).join(' ')).split(/[\s-]+/).filter(Boolean));

/**
 * Страница списка: { items, page, pageCount, total, from, to }.
 *
 * Номер страницы прижимается к границам: после поиска на пятой странице
 * найденное может уместиться на одной, и «страница 5 из 1» была бы пустым
 * экраном без объяснения.
 */
export function paginate(rows, page, size) {
    const list = rows || [];
    const total = list.length;
    const pageCount = Math.max(1, Math.ceil(total / size));
    const current = Math.min(Math.max(1, Number(page) || 1), pageCount);
    const start = (current - 1) * size;
    const items = list.slice(start, start + size);
    return {
        items, page: current, pageCount, total,
        from: total ? start + 1 : 0, to: start + items.length,
    };
}
