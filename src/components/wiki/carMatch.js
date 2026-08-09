/* Распознавание марки и модели машины в поисковом запросе.
 *
 * Отдельным модулем от WikiSearchModal, потому что это чистая функция над
 * справочником классификатора: её можно и нужно гонять тестом (node --test
 * tests/wiki_car_match.test.mjs), а компонент тянет за собой React и axios.
 *
 * Запрос сначала разворачивается в варианты написания (searchText.js — тот же
 * словарь, что у сервера), поэтому «rfvhb», «камри» и «camry» приводят к одной
 * машине.
 */

import { queryVariants } from './searchText.js';

/* Имя для сравнения: регистр вниз, апостроф ВЫРЕЗАЕТСЯ.
 *
 * Нормализация запроса заменяет апостроф пробелом, и «Cee'd» распадалось на
 * «cee» и «d»; однобуквенное «d» входит в половину справочника, поэтому поиск
 * по Cee'd отвечал Datsun on-DO. Здесь апостроф именно удаляется — тогда
 * «ceed» === key("Cee'd").
 */
export const carKey = (value) => String(value || '').toLowerCase().replace(/['’]/g, '');

const fullName = (car) => `${carKey(car.brand)} ${carKey(car.model)}`;

/** Варианты написания запроса, приведённые к ключам сравнения. */
const keyedVariants = (query) => {
    const trimmed = String(query || '').trim();
    if (trimmed.length < 2) return [];
    const seen = new Set();
    const out = [];
    for (const variant of queryVariants(trimmed)) {
        const key = carKey(variant).trim();
        if (key && !seen.has(key)) { seen.add(key); out.push(key); }
    }
    return out;
};

/** Машина по запросу — или null.
 *
 * Проходы идут ПО ОЧЕРЕДИ и каждый — по всему справочнику. В оригинале оба
 * условия висели в одном цикле по машинам, поэтому побеждала не самая точная
 * машина, а самая ранняя в файле: «рио» открывало Daihatsu Terios (подстрока
 * te-RIO-s), «хундай солярис» — Hyundai Accent (вариант-алиас 'hyundai' равен
 * марке и срабатывал на первой же машине Hyundai).
 */
export function matchCar(cars, query) {
    if (!cars?.length) return null;
    const variants = keyedVariants(query);
    if (!variants.length) return null;

    const find = (predicate) => {
        for (const variant of variants) {
            for (const car of cars) {
                if (predicate(variant, car)) return car;
            }
        }
        return null;
    };

    // 1. Точное «марка модель».
    const exactFull = find((v, car) => fullName(car) === v);
    if (exactFull) return exactFull;

    // 2. Точная модель.
    const exactModel = find((v, car) => carKey(car.model) === v);
    if (exactModel) return exactModel;

    // 3. Многословный вариант: одни слова дают марку, другие — модель.
    const byWords = find((v, car) => {
        const words = v.split(/\s+/).filter((w) => w.length >= 2);
        if (words.length < 2) return false;
        const brand = carKey(car.brand);
        const model = carKey(car.model);
        return words.some((w) => brand.includes(w) || w.includes(brand))
            && words.some((w) => model.includes(w) || w.includes(model));
    });
    if (byWords) return byWords;

    // 4. Начало слова: «кам» -> Camry, «сол» -> Solaris. Именно НАЧАЛО, а не
    //    вхождение куда угодно, иначе «рио» снова поймает «Terios».
    const byPrefix = find((v, car) => fullName(car).split(' ')
        .some((word) => word.startsWith(v)));
    if (byPrefix) return byPrefix;

    // 5. Марка без модели: показываем первую модель марки, список — рядом.
    return find((v, car) => {
        const brand = carKey(car.brand);
        return brand === v || brand.startsWith(v);
    });
}

/** Марка по запросу — для списка «Модели X в классификаторе». */
export function matchBrand(cars, query) {
    if (!cars?.length) return null;
    const variants = keyedVariants(query);
    if (!variants.length) return null;
    const brands = Array.from(new Set(cars.map((c) => c.brand)));

    for (const variant of variants) {
        const hit = brands.find((brand) => {
            const key = carKey(brand);
            return key === variant || key.startsWith(variant);
        });
        if (hit) return hit;
    }
    // Составной запрос «марка модель»: марку ищем среди его слов.
    for (const variant of variants) {
        for (const word of variant.split(/\s+/)) {
            if (word.length < 2) continue;
            const hit = brands.find((brand) => carKey(brand).startsWith(word));
            if (hit) return hit;
        }
    }
    return null;
}
