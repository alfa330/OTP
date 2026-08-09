/* Варианты написания запроса: нормализация, транслитерация, раскладка, алиасы.
 *
 * Клиентский близнец wiki/text.py (который сам — порт utils/text.ts исходной
 * вики). Нужен там, где сервер не участвует: распознавание машины в поисковой
 * строке идёт по локальному справочнику классификатора, без запроса к базе.
 *
 * СЛОВАРЬ ALIAS_GROUPS обязан совпадать с wiki/text.py дословно — за этим
 * следит тест tests/test_wiki_search.py::JsAliasSyncTest, который читает этот
 * файл как текст. Меняете группу здесь — меняйте и там.
 */

export const CYRILLIC_TO_LATIN = {
    'а': 'a', 'ә': 'ae', 'б': 'b', 'в': 'v', 'г': 'g', 'ғ': 'gh', 'д': 'd',
    'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k',
    'қ': 'q', 'л': 'l', 'м': 'm', 'н': 'n', 'ң': 'ng', 'о': 'o', 'ө': 'oe',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ұ': 'u', 'ү': 'ue',
    'ф': 'f', 'х': 'kh', 'һ': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'і': 'i', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
};

// Порядок важен: сначала длинные сочетания, иначе 'sh' съест часть 'shch'.
export const LATIN_TO_CYRILLIC = [
    ['shch', 'щ'], ['sh', 'ш'], ['ch', 'ч'], ['zh', 'ж'], ['kh', 'х'],
    ['ts', 'ц'], ['yu', 'ю'], ['ya', 'я'], ['gh', 'ғ'], ['ng', 'ң'],
    ['oe', 'ө'], ['ae', 'ә'], ['ue', 'ү'],
    ['a', 'а'], ['b', 'б'], ['v', 'в'], ['g', 'г'], ['d', 'д'], ['e', 'е'],
    ['z', 'з'], ['i', 'и'], ['y', 'ы'], ['k', 'к'], ['q', 'қ'], ['l', 'л'],
    ['m', 'м'], ['n', 'н'], ['o', 'о'], ['p', 'п'], ['r', 'р'], ['s', 'с'],
    ['t', 'т'], ['u', 'у'], ['f', 'ф'], ['h', 'һ'], ['c', 'к'],
];

// Раскладка: набрано латиницей вместо кириллицы («ntrcn» вместо «текст»).
export const EN_TO_RU_LAYOUT = {
    'q': 'й', 'w': 'ц', 'e': 'у', 'r': 'к', 't': 'е', 'y': 'н', 'u': 'г',
    'i': 'ш', 'o': 'щ', 'p': 'з', '[': 'х', ']': 'ъ',
    'a': 'ф', 's': 'ы', 'd': 'в', 'f': 'а', 'g': 'п', 'h': 'р', 'j': 'о',
    'k': 'л', 'l': 'д', ';': 'ж', "'": 'э',
    'z': 'я', 'x': 'ч', 'c': 'с', 'v': 'м', 'b': 'и', 'n': 'т', 'm': 'ь',
    ',': 'б', '.': 'ю', '/': '.',
};

export const ALIAS_GROUPS = [
    ['meilisearch', 'meili', 'мейлисерч', 'меилисерч'],
    ['render', 'render.com', 'рендер'],
    ['vercel', 'верцел'],
    ['docker', 'докер'],
    ['postgres', 'postgresql', 'постгрес'],
    ['supabase', 'супабейс'],
    ['wireguard', 'вайргард'],
    ['wifi', 'wi-fi', 'вайфай', 'вай фай'],
    ['github', 'git hub', 'гитхаб'],
    ['node', 'nodejs', 'node.js', 'нода'],
    ['react', 'reactjs', 'реакт'],
    ['python', 'питон'],
    ['flask', 'фласк'],
    ['django', 'джанго'],
    ['redis', 'редис'],
    ['elastic', 'elasticsearch', 'эластик'],
    ['postman', 'постман'],

    ['toyota', 'тойота'],
    ['hyundai', 'хендай', 'хёндай', 'хундай'],
    ['kia', 'киа', 'кио'],
    ['volkswagen', 'vw', 'фольксваген', 'фольц'],
    ['mercedes', 'mercedes-benz', 'benz', 'мерседес', 'мерс'],
    ['bmw', 'бмв'],
    ['audi', 'ауди'],
    ['lexus', 'лексус'],
    ['nissan', 'ниссан'],
    ['chevrolet', 'шевроле', 'шеви'],
    ['mitsubishi', 'митсубиси', 'митсубиши', 'митсу'],
    ['subaru', 'субару'],
    ['mazda', 'мазда'],
    ['honda', 'хонда'],
    ['land rover', 'range rover', 'ленд ровер', 'ренж ровер', 'лендровер'],
    ['renault', 'рено'],
    ['peugeot', 'пежо'],
    ['citroen', 'ситроен'],
    ['opel', 'опель'],
    ['suzuki', 'сузуки'],
    ['skoda', 'шкода'],
    ['chery', 'чери'],
    ['geely', 'джили'],
    ['changan', 'чанган'],
    ['byd', 'бивайди'],
    ['infiniti', 'инфинити'],
    ['cadillac', 'кадиллак'],
    ['dodge', 'додж'],
    ['porsche', 'порше'],
    ['tesla', 'тесла'],
    ['lada', 'лада', 'ваз'],

    ['camry', 'камри'],
    ['solaris', 'солярис'],
    ['accent', 'акцент'],
    ['rio', 'рио'],
    ['optima', 'оптима'],
    ['elantra', 'элантра'],
    ['sonata', 'соната'],
    ['octavia', 'октавия'],
    ['rapid', 'рапид'],
    ['superb', 'суперб'],
    ['astra', 'астра'],
    ['vectra', 'вектра'],
    ['zafira', 'зафира'],
    ['golf', 'гольф'],
    ['passat', 'пассат'],
    ['polo', 'поло'],
    ['tiguan', 'тигуан'],
    ['touareg', 'туарег'],
    ['cruze', 'круз'],
    ['cobalt', 'кобальт'],
    ['aveo', 'авео'],
    ['spark', 'спарк'],
    ['malibu', 'малибу'],
    ['captiva', 'каптива'],
    ['nexia', 'нексия'],
    ['matiz', 'матиз'],
    ['gentra', 'джентра'],
    ['priora', 'приора'],
    ['granta', 'гранта'],
    ['vesta', 'веста'],
    ['kalina', 'калина'],
    ['largus', 'ларгус'],
];

// Буквы русского, казахского и английского алфавитов, цифры, пробел и дефис.
const ALLOWED = /[^a-zа-яёәғқңөұүһі0-9\s-]/g;
const SPACES = /\s+/g;
const WORD_SPLIT = /[\s-]+/;

/** Регистр, ё -> е (кириллическая — как в wiki/text.py), мусор -> пробел. */
export function normalizeText(text) {
    return String(text || '')
        .toLowerCase()
        .replace(/ё/g, 'е')
        .replace(ALLOWED, ' ')
        .replace(SPACES, ' ')
        .trim();
}

// Слово/фраза -> все прочие написания той же сущности. Как и на сервере,
// ключи нормализуются той же функцией, что и запрос. Многословные написания
// храним отдельно, чтобы «example.com» не совпадал с «render.com» по «com».
const ALIAS_INDEX = new Map();
const PHRASE_INDEX = new Map();
for (const group of ALIAS_GROUPS) {
    for (const word of group) {
        const parts = normalizeText(word).split(WORD_SPLIT).filter(Boolean);
        if (!parts.length) continue;
        const isPhrase = parts.length > 1;
        const key = parts.join(' ');
        if (!isPhrase && key.length < 2) continue;
        const index = isPhrase ? PHRASE_INDEX : ALIAS_INDEX;
        if (!index.has(key)) index.set(key, new Set());
        const bucket = index.get(key);
        for (const alias of group) {
            if (alias !== word) bucket.add(alias);
        }
    }
}

export function transliterateCyrillicToLatin(text) {
    if (!text) return '';
    let out = '';
    for (const char of String(text)) {
        out += CYRILLIC_TO_LATIN[char] !== undefined ? CYRILLIC_TO_LATIN[char] : char;
    }
    return out;
}

export function transliterateLatinToCyrillic(text) {
    if (!text) return '';
    let result = String(text);
    for (const [latin, cyrillic] of LATIN_TO_CYRILLIC) {
        result = result.split(latin).join(cyrillic);
    }
    return result;
}

/** «ntrcn» -> «текст»: человек забыл переключить раскладку. */
export function fixKeyboardLayout(text) {
    if (!text) return '';
    let out = '';
    for (const char of String(text)) {
        const low = char.toLowerCase();
        out += EN_TO_RU_LAYOUT[low] !== undefined ? EN_TO_RU_LAYOUT[low] : char;
    }
    return out;
}

/** Все альтернативные написания слов из текста. */
export function aliasesForText(text) {
    const matched = new Set();
    const words = normalizeText(text).split(WORD_SPLIT).filter(Boolean);
    for (const word of words) {
        if (word.length < 2) continue;
        const bucket = ALIAS_INDEX.get(word);
        if (bucket) for (const alias of bucket) matched.add(alias);
    }

    const paddedText = ` ${words.join(' ')} `;
    for (const [phrase, bucket] of PHRASE_INDEX) {
        if (!paddedText.includes(` ${phrase} `)) continue;
        for (const alias of bucket) matched.add(alias);
    }
    return Array.from(matched).sort();
}

/** Варианты написания запроса, в порядке убывания близости к оригиналу. */
export function queryVariants(query) {
    const variants = [];
    const add = (value) => {
        const trimmed = String(value || '').trim();
        if (trimmed && !variants.includes(trimmed)) variants.push(trimmed);
    };

    add(query);
    const normalized = normalizeText(query);
    add(normalized);
    add(transliterateCyrillicToLatin(normalized));
    add(transliterateLatinToCyrillic(normalized));

    const fixedLayout = fixKeyboardLayout(query);
    add(fixedLayout);
    const normalizedFixed = normalizeText(fixedLayout);
    add(normalizedFixed);
    add(transliterateCyrillicToLatin(normalizedFixed));

    for (const alias of aliasesForText(normalized)) add(alias);
    for (const alias of aliasesForText(normalizedFixed)) add(alias);

    return variants;
}
