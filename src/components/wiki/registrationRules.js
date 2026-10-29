/* Регистрация водителей в карточке города — правила показа, отдельно от JSX,
 * чтобы их можно было проверить без браузера (tests/wiki_registration_rules.test.mjs).
 *
 * Куда регистрировать, решает сервер (wiki/registration.py) — одним правилом
 * для карточки, поиска и помощника: своя диспетчерская парка в городе, иначе
 * города «Куда регистрировать» по порядку, иначе «нет». Здесь — только как
 * это сказать по-русски и что свернуть.
 */

import { cityGenitive } from './cityRules';

/** Предложный падеж для «Регистрируем в Костанае».
 *
 *  Правила, верные для городов страны: несклоняемые на гласную (Алматы,
 *  Актобе, Атырау, Кокшетау) остаются как есть; «-ый» → «-ом» (Рудном);
 *  «-а» → «-е» (Караганде, Астане); «-й» → «-е» (Семее, Костанае); на
 *  согласную — «+е» (Шымкенте, Усть-Каменогорске). Двойник cityGenitive. */
export const cityLocative = (name) => {
    const text = String(name || '').trim();
    if (!text) return '';
    const lower = text.toLowerCase();
    if (/ый$/.test(lower)) return `${text.slice(0, -2)}ом`;
    if (/а$/.test(lower)) return `${text.slice(0, -1)}е`;
    if (/й$/.test(lower)) return `${text.slice(0, -1)}е`;
    if (/[бвгджзклмнпрстфхцчшщ]$/.test(lower)) return `${text}е`;
    return text;
};

export const plural = (count, one, few, many) => {
    const tail = Math.abs(count) % 100;
    if (tail >= 11 && tail <= 14) return many;
    const last = tail % 10;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

export const dispatcherWord = (count) => plural(count, 'диспетчерская', 'диспетчерские', 'диспетчерских');

/** «Караганде», «Туркестане и Шымкенте», «Астане, Актобе и Уральске». */
const listLocative = (names) => {
    const words = names.map(cityLocative);
    return words.length > 1 ? `${words.slice(0, -1).join(', ')} и ${words[words.length - 1]}` : words[0] || '';
};

/** Города, куда регистрируют парки без своей диспетчерской здесь, — по порядку. */
export const otherCities = (block) => {
    const found = [];
    (block?.parks || []).forEach((entry) => {
        // Ближайшие диспетчерские — не «куда регистрируют» город, а запасной путь.
        if (!entry.own && !entry.nearest && entry.city?.name && !found.includes(entry.city.name)) {
            found.push(entry.city.name);
        }
    });
    return found;
};

/** Шапка блока: что сказать первым — город, куда регистрировать.
 *  tone: own — своя диспетчерская, mixed — своя есть не у всех парков,
 *  elsewhere — в другой город, unset — правило не задано. */
export const registrationHeadline = (cityName, block) => {
    if (!block) return null;
    const targets = (block.targets || []).map((target) => target.name);
    if (block.state === 'mixed') {
        /* Темиртау: своя диспетчерская только у Честного, iTaxi и Аманат — в
           Караганде. «Регистрируем в Темиртау» было бы неправдой для двух
           парков из трёх. */
        const own = (block.parks || []).filter((entry) => entry.own).length;
        const others = otherCities(block);
        return {
            tone: 'mixed',
            title: `Регистрируем в ${listLocative([cityName, ...others])}`,
            subtitle: `Своя диспетчерская — у ${own} ${plural(own, 'парка', 'парков', 'парков')}, `
                + `у остальных — в ${listLocative(others)}`,
        };
    }
    if (block.state === 'own') {
        return {
            tone: 'own',
            title: `Регистрируем в ${cityLocative(cityName)}`,
            subtitle: `${block.own_count} ${dispatcherWord(block.own_count)} в городе`,
        };
    }
    if (block.state === 'elsewhere' && targets.length) {
        const chain = targets.slice(1).map((name, index) => (
            `если у парка нет диспетчерской в ${cityLocative(targets[index])} — в ${cityLocative(name)}`));
        return {
            tone: 'elsewhere',
            title: `Регистрируем в ${cityLocative(targets[0])}`,
            subtitle: [`Своих диспетчерских в ${cityLocative(cityName)} нет`, ...chain].join(' · '),
        };
    }
    return {
        tone: 'unset',
        title: 'Куда регистрировать — не задано',
        subtitle: `Своих диспетчерских в ${cityLocative(cityName)} нет`,
    };
};

/** Подпись диспетчерской: город кабинета — только когда он другой
 *  («Честный Темиртау» записан в кабинете в Караганду). */
export const dispatcherNote = (dispatcher) => (
    dispatcher?.fleet_city_other ? `в кабинете — ${dispatcher.fleet_city_other}` : ''
);

/** Строки парков: свои и по списку «куда регистрировать» — ответ города,
 *  ближайшие диспетчерские остальных парков — запасной путь, отдельно. */
export const splitNearest = (parks) => ({
    main: (parks || []).filter((entry) => !entry.nearest),
    nearest: (parks || []).filter((entry) => entry.nearest),
});

/** «Остальные 14 парков — ближайшая диспетчерская»; один — «Ещё 1 парк»
 *  («остальные 1 парк» не по-русски). В городе без своих и без правила других
 *  строк нет, и «остальные» было бы не к чему. */
export const nearestLabel = (count, alone = false) => {
    const parks = `${count} ${plural(count, 'парк', 'парка', 'парков')}`;
    const head = alone ? parks : `${count === 1 ? 'Ещё' : 'Остальные'} ${parks}`;
    return `${head} — ближайшая диспетчерская`;
};

/** Сколько строк парков показать сразу. В Алматы их семнадцать — карточка
 *  превратилась бы в список, а смотрят обычно один-два первых. */
export const PARKS_PREVIEW = 6;

export const parksPreview = (parks, expanded) => {
    const all = parks || [];
    if (expanded || all.length <= PARKS_PREVIEW + 1) return { shown: all, hidden: 0 };
    return { shown: all.slice(0, PARKS_PREVIEW), hidden: all.length - PARKS_PREVIEW };
};

/** «Нет диспетчерской для Рудного: Jana такси, Global и ещё 9». */
export const absentText = (cityName, absent, limit = 4) => {
    const list = absent || [];
    if (!list.length) return '';
    const head = list.slice(0, limit).join(', ');
    const rest = list.length - limit;
    return `Нет диспетчерской для ${cityGenitive(cityName)}: ${head}${rest > 0 ? ` и ещё ${rest}` : ''}`;
};

/** «Сюда же регистрируют водителей из Рудного и Темиртау». */
export const feedersText = (feeders) => {
    const names = (feeders || []).map((feeder) => cityGenitive(feeder.name));
    if (!names.length) return '';
    const list = names.length === 1 ? names[0]
        : `${names.slice(0, -1).join(', ')} и ${names[names.length - 1]}`;
    return `Сюда же регистрируют водителей из ${list}`;
};

/** Строка поиска «Регистрация»: заголовок и подпись. */
export const registrationSearchText = (entry) => {
    const dispatchers = (entry?.dispatchers || []).map((item) => `«${item.name}»`).join(', ');
    if (entry?.park) {
        const title = `Регистрация ${entry.park} · ${entry.name}`;
        if (!entry.target) return { title, subtitle: `Диспетчерской ${entry.park} для этого города нет` };
        if (entry.nearest) {
            return {
                title,
                subtitle: [`Ближайшая — в ${cityLocative(entry.target)}`, entry.km ? `~${entry.km} км` : '',
                    dispatchers].filter(Boolean).join(' · '),
            };
        }
        const where = entry.own ? 'Здесь' : `В ${cityLocative(entry.target)}`;
        return { title, subtitle: [where, dispatchers].filter(Boolean).join(' · ') };
    }
    const title = `Регистрация водителей · ${entry?.name || ''}`;
    if (entry?.state === 'own') {
        const count = (entry.dispatchers || []).length;
        return { title, subtitle: `Регистрируем здесь · ${count} ${plural(count, 'парк', 'парка', 'парков')}` };
    }
    if (entry?.state === 'mixed') {
        return { title, subtitle: `Зависит от парка: здесь или в ${listLocative(entry.others || [])}` };
    }
    if (entry?.state === 'elsewhere' && (entry.targets || []).length) {
        return {
            title,
            subtitle: `Своих диспетчерских нет — в ${entry.targets.map(cityLocative).join(', затем в ')}`,
        };
    }
    return { title, subtitle: 'Своих диспетчерских нет, город регистрации не задан' };
};
