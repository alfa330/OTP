/* «Классификатор авто» на экране: загрузка справочника города и расчёт по году.
 *
 * Справочник отдаёт сервер — GET /api/wiki/classifier?city=<id>
 * (wiki/routes_classifier.py). Это слепок базы знаний Яндекс Про по всем
 * городам, тот же, из которого собрана книга помощника вики: экран и помощник
 * не могут назвать разный год. Прежде экран носил в бандле файл из Wiki 2.0 с
 * годами одного Алматы и «поправкой города» — и на трети пар «машина × город ×
 * тариф» показывал неверный год.
 *
 * Модуль без React — его гоняет node --test (tests/classifier_data.test.mjs).
 * requirementOf и verdictOf — двойники wiki/classifier.requirement и verdict:
 * год меняют по цифре, и спрашивать сервер на каждую цифру незачем. Их
 * согласие с питоном сторожит общая выборка tests/data/classifier_parity.json
 * (её же пересчитывает tests/test_classifier_parity_fixture.py).
 */

import axios from 'axios';

/* Кэшируется ПРОМИС, по ключу «адрес|город»: город, открытый второй раз,
   приходит мгновенно, а два одновременных запроса одного города — один.
   Отказ из кэша вычищается: иначе один сбой сети выключал бы классификатор
   до перезагрузки вкладки (ровно так однажды залипал справочник поиска). */
const cache = new Map();

const defaultFetcher = (url, options) => axios.get(url, options).then((response) => response.data);

export function loadClassifier({ base, headers, city = 'almaty', fetcher = defaultFetcher }) {
    const key = `${base}|${city}`;
    if (!cache.has(key)) {
        const promise = Promise.resolve()
            .then(() => fetcher(`${base}/classifier`, { headers, params: { city } }))
            .catch((error) => {
                cache.delete(key);
                // 404 с кодом WIKI_CLASSIFIER_HIDDEN — статья-классификатор этому
                // человеку не видна (граница пространства, запрет на статью). Это
                // не сбой: экран молча не показывает машину. Голый 404 без кода —
                // ручки ещё нет (фронт выкатился раньше сервера): это сбой, его
                // повторяют, а не прячут классификатор до перезагрузки.
                if (error?.response?.status === 404
                    && error?.response?.data?.code === 'WIKI_CLASSIFIER_HIDDEN') {
                    const hidden = new Error('Классификатор авто недоступен');
                    hidden.hidden = true;
                    throw hidden;
                }
                throw error;
            });
        cache.set(key, promise);
    }
    return cache.get(key);
}

/** Для тестов: забыть всё загруженное. */
export function forgetClassifier() {
    cache.clear();
}

/* Указатель машин общий для всех городов ОДНОГО слепка. Номер машины берётся
   всегда из того ответа, по которому считается тариф: ночная сверка может
   сменить слепок между двумя городами, и номер из чужого ответа указал бы на
   другую машину. Поэтому машина на экране — это марка и модель, а номер
   ищется заново в каждом ответе. */
const carLists = new WeakMap();
const carIndexes = new WeakMap();
const rowMaps = new WeakMap();

const nameKey = (brand, model) => `${brand}\u0000${model}`;

export function carsOf(payload) {
    if (!payload) return [];
    let cars = carLists.get(payload);
    if (!cars) {
        cars = (payload.cars || []).map(([brand, model], index) => ({ index, brand, model }));
        carLists.set(payload, cars);
    }
    return cars;
}

function carIndex(payload, car) {
    if (!payload || !car) return -1;
    let map = carIndexes.get(payload);
    if (!map) {
        map = new Map();
        (payload.cars || []).forEach(([brand, model], index) => map.set(nameKey(brand, model), index));
        carIndexes.set(payload, map);
    }
    const found = map.get(nameKey(car.brand, car.model));
    return found === undefined ? -1 : found;
}

function rowsOf(entry) {
    let map = rowMaps.get(entry);
    if (!map) {
        map = new Map();
        (entry.rows || []).forEach((row) => map.set(row[0], row));
        rowMaps.set(entry, map);
    }
    return map;
}

function requirementFrom(item, reqs) {
    const extra = item.length > 1 ? ((reqs || [])[item[1]] || {}) : {};
    return { year: item[0], text: extra.text || null, except: extra.except || null };
}

/* Что тариф города требует от машины — или null, если тарифа в городе нет.
   kind: 'row' — строка таблицы; 'brand' — правило марки («LADA (кроме указанных
   моделей)»); 'other' — строка «Другие модели»; 'absent' — машины в таблице нет;
   'any' — у тарифа правило «любая модель не старше N года». year: 0 — не
   допускается; null — требование не разобрано (показывается text). */
function requirementIn(entry, payload, car) {
    if (!entry) return null;
    if (entry.kind === 'any') return { kind: 'any', year: entry.any, text: null, except: null };
    const index = carIndex(payload, car);
    const row = index >= 0 ? rowsOf(entry).get(index) : undefined;
    if (row) return { kind: 'row', ...requirementFrom(row.slice(1), payload.reqs) };
    const brandRule = car && entry.brands ? entry.brands[car.brand] : undefined;
    if (brandRule) return { kind: 'brand', ...requirementFrom(brandRule, payload.reqs) };
    if (entry.other) return { kind: 'other', ...requirementFrom(entry.other, payload.reqs) };
    return { kind: 'absent', year: 0, text: null, except: null };
}

export function requirementOf(payload, car, key) {
    return requirementIn(payload?.city?.tariffs?.[key], payload, car);
}

/** Новые правила, которые ещё не вступили: { from, req } или null. */
export function upcomingOf(payload, car, key) {
    const next = payload?.city?.tariffs?.[key]?.next;
    if (!next || !next.from) return null;
    const req = requirementIn(next, payload, car);
    return req ? { from: next.from, req } : null;
}

/* Машина такого года в тариф: 'fits' — подходит; 'except' — только если уже
   зарегистрирована в сервисе до даты исключения; 'old' — нужен год новее;
   'no' — не допускается; 'unknown' — требование не разобрано; null — тарифа нет. */
export function verdictOf(req, year) {
    if (!req) return null;
    if (req.year === null || req.year === undefined) return 'unknown';
    if (req.year && year >= req.year) return 'fits';
    const range = req.except;
    if (range && range[0] <= year && year <= range[1]) return 'except';
    return req.year ? 'old' : 'no';
}

/* Условие, без которого «подходит» неверно: «только комплектации с сидениями,
   не в спортивном стиле». Его показывают в самой строке, а не под «i»: ответ
   без него вводит в заблуждение. Оговорка-исключение («а также машины 2023
   года, зарегистрированные до…») к подходящему году не относится — её нет. */
export function conditionOf(req) {
    if (!req || !req.text || req.except) return '';
    const match = /\(([^()]+)\)\s*\.?\s*$/.exec(req.text);
    return match ? match[1].trim() : req.text;
}

/** Берёт ли тариф эту модель хоть какого-то года (с оговорками). */
export function allows(req) {
    return !!req && (req.year === null || req.year === undefined || req.year > 0 || !!req.except);
}

/* Допущена ли модель для НОВЫХ подключений: год «от N» (или требование, которое
   показывается текстом). Строка «не допускается, кроме машин, зарегистрированных
   до даты» сюда не входит: новую такую машину уже не подключить. По этому правилу
   считаются «468 моделей» тарифа и список без фильтра — двойник
   wiki/classifier.admitted. */
export function admitted(req) {
    return !!req && (req.year === null || req.year === undefined || req.year > 0);
}

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
    'сентября', 'октября', 'ноября', 'декабря'];

/** '2026-06-30' → «30 июня 2026» — так дату пишет и сама таблица Яндекса. */
export function dateWords(iso) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
    if (!match) return String(iso || '');
    return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}`;
}

export function plural(number, one, few, many) {
    const tail = number % 100;
    if (tail >= 11 && tail <= 14) return many;
    const last = number % 10;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
}

/** Требование коротко: «от 2014», «не допускается», «любая модель от 1988». */
export function shortRequirement(req) {
    if (!req) return '';
    if (req.kind === 'any') return `любая модель от ${req.year}`;
    if (req.kind === 'absent') return 'нет в списке тарифа';
    if (req.year === null || req.year === undefined) return req.text || 'требование не указано';
    return req.year ? `от ${req.year}` : 'не допускается';
}

/** Сводка тарифа города для карточки списка: «468 моделей» или «любая модель от 1988 г.». */
export function tariffSummary(entry) {
    if (!entry) return '';
    if (entry.kind === 'any') return `Любая модель от ${entry.any} г.`;
    const count = entry.count ?? (entry.rows || []).filter((row) => row[1] !== 0).length;
    let text = `${count.toLocaleString('ru-RU')} ${plural(count, 'модель', 'модели', 'моделей')}`;
    if (entry.other) {
        text += entry.other[0] ? `, остальные — от ${entry.other[0]} г.` : ', остальные не допускаются';
    }
    return text;
}

/* Строки списка тарифа города: [{ brand, model, req }], по марке и модели.
   Без фильтра — только модели, которые тариф берёт: «какие машины берут в
   Комфорт» — вопрос про допущенные, и сотни строк «не допускается» его топят.
   С фильтром — и недопущенные тоже: на «cobalt» человек должен увидеть
   «не допускается», а не «ничего не найдено». */
export function listRows(payload, key, query = '') {
    const entry = payload?.city?.tariffs?.[key];
    if (!entry || entry.kind !== 'list') return [];
    const needle = String(query || '').trim().toLowerCase();
    const cars = payload.cars || [];
    const out = [];
    (entry.rows || []).forEach((row) => {
        const pair = cars[row[0]];
        if (!pair) return;
        const [brand, model] = pair;
        const req = { kind: 'row', ...requirementFrom(row.slice(1), payload.reqs) };
        if (needle) {
            const name = `${brand} ${model}`.toLowerCase();
            if (!name.includes(needle) && !model.toLowerCase().includes(needle)) return;
        } else if (!admitted(req)) {
            return;
        }
        out.push({ brand, model, req });
    });
    return out.sort((a, b) => a.brand.localeCompare(b.brand, 'ru')
        || a.model.localeCompare(b.model, 'ru', { numeric: true }));
}

/** Тарифы города по порядку справочника: [{ key, name, entry }]. */
export function cityTariffs(payload) {
    const own = payload?.city?.tariffs || {};
    return (payload?.tariffs || [])
        .filter((tariff) => own[tariff.key])
        .map((tariff) => ({ key: tariff.key, name: tariff.name, entry: own[tariff.key] }));
}
