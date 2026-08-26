/* Фильтры поиска по вике: одно описание на оба экрана поиска.
 *
 * Поисков в разделе ДВА — поле в шапке (WikiSearch) и витрина «Все статьи вики»
 * (WikiLibrary). Набор фильтров, подписи и сборка адреса живут здесь, потому
 * что два места, знающие одно и то же, однажды разойдутся: у одного появится
 * третий тип документа, у другого нет, и человек получит разную выдачу на один
 * запрос в зависимости от того, где его набрал.
 *
 * Модуль чистый — ни React, ни axios: его и проверяет тест
 * tests/wiki_search_filters.test.mjs.
 */

import { FILTERABLE_TYPES, typePlural } from './articleTypes.js';

/* Область поиска. Значения совпадают с wiki/search.py (MATCH_ALL, MATCH_TITLE)
   и уезжают в адрес как ?match= — НЕ как ?scope=: ?scope=all там уже занят
   переключателем периметра «показать весь портал», и второе значение в том же
   слове тихо ломало бы одно из двух. */
export const MATCH_ALL = 'all';
export const MATCH_TITLE = 'title';

export const MATCH_OPTIONS = [
    { value: MATCH_ALL, label: 'По тексту' },
    { value: MATCH_TITLE, label: 'По названиям' },
];

/** Пустые фильтры — «ищем везде и всё». Заморожены: значение общее на два
 *  экрана, и правка на месте из одного из них была бы правкой обоих. */
export const EMPTY_FILTERS = Object.freeze({
    types: Object.freeze([]),
    authors: Object.freeze([]),
    match: MATCH_ALL,
});

const list = (value) => (Array.isArray(value) ? value : []);

/** Фильтры, приведённые к рабочему виду: недостающие поля — как пустые. */
export const normalizeFilters = (filters) => ({
    types: list(filters?.types),
    authors: list(filters?.authors),
    match: filters?.match === MATCH_TITLE ? MATCH_TITLE : MATCH_ALL,
});

/** Сколько фильтров задано. Область считается ОДНИМ, а не по числу вариантов:
 *  это цифра на кнопке, и «2» при одном выбранном типе сбивала бы с толку. */
export const activeCount = (filters) => {
    const value = normalizeFilters(filters);
    return value.types.length + value.authors.length
        + (value.match === MATCH_ALL ? 0 : 1);
};

/** Ничего не выбрано — кнопка фильтров стоит серой, а выдача полная. */
export const isDefaultFilters = (filters) => activeCount(filters) === 0;

/** Переключить значение в списке. Порядок сохраняется: он уезжает в адрес,
 *  и одинаковые наборы должны давать одинаковый адрес. */
export const toggleValue = (values, value) => (
    list(values).includes(value)
        ? list(values).filter((item) => item !== value)
        : list(values).concat([value])
);

/* Параметры адреса собираются через URLSearchParams, а не объектом для axios,
   и это не вкусовщина: axios по умолчанию раскладывает массив как
   `article_type[]=a&article_type[]=b`, а Flask читает их через
   request.args.getlist('article_type') — то есть НЕ увидит ни одного значения,
   и фильтр молча перестанет работать. URLSearchParams повторяет ключ как есть.

   axios принимает URLSearchParams в params напрямую. */
export const searchParams = (term, filters, extra = {}) => {
    const value = normalizeFilters(filters);
    const params = new URLSearchParams();
    params.set('q', String(term ?? '').trim());
    Object.entries(extra).forEach(([key, item]) => {
        // null и undefined не отправляем: у space_id это обычное состояние
        // «пространство ещё не известно», и `space_id=null` сервер прочитал бы
        // как строку.
        if (item !== null && item !== undefined && item !== '') params.set(key, String(item));
    });
    value.types.forEach((type) => params.append('article_type', type));
    value.authors.forEach((id) => params.append('author_id', String(id)));
    if (value.match !== MATCH_ALL) params.set('match', value.match);
    return params;
};

/* Типы, по которым имеет смысл фильтровать. Тот же список, что у витрины:
   «Обычная статья» — это отсутствие типа, и фильтровать по ней нечего.

   Подпись во МНОЖЕСТВЕННОМ числе («Регламенты», а не «Регламент»): фильтр
   называет НАБОР статей, который останется в выдаче, а не одну штуку. Берётся
   из того же articleTypes.js, что и заголовки подборок в каталоге. */
export const TYPE_OPTIONS = FILTERABLE_TYPES.map((type) => ({
    value: type.value, label: typePlural(type.value), tone: type.tone,
}));

const TYPE_LABEL = new Map(TYPE_OPTIONS.map((type) => [type.value, type.label]));

/** Выбранное — строкой чипов: [{ key, kind, label }].
 *
 * Собирается здесь, а не в разметке: чипы стоят на двух экранах, а подпись
 * автора приходит из отдельного запроса и может ещё не доехать — тогда вместо
 * имени показываем нейтральное «Создатель», а не пустое место.
 */
export const filterChips = (filters, authors = []) => {
    const value = normalizeFilters(filters);
    const names = new Map(list(authors).map((a) => [String(a.id), a.name]));
    const chips = [];
    if (value.match !== MATCH_ALL) {
        chips.push({ key: 'match', kind: 'match', label: 'Только в названиях' });
    }
    value.types.forEach((type) => chips.push({
        key: `type:${type}`, kind: 'type', value: type,
        label: TYPE_LABEL.get(type) || type,
    }));
    value.authors.forEach((id) => chips.push({
        key: `author:${id}`, kind: 'author', value: id,
        label: names.get(String(id)) || 'Создатель',
    }));
    return chips;
};

/** Снять один чип — обратная операция к filterChips. */
export const withoutChip = (filters, chip) => {
    const value = normalizeFilters(filters);
    if (chip?.kind === 'match') return { ...value, match: MATCH_ALL };
    if (chip?.kind === 'type') {
        return { ...value, types: value.types.filter((t) => t !== chip.value) };
    }
    if (chip?.kind === 'author') {
        return { ...value, authors: value.authors.filter((a) => a !== chip.value) };
    }
    return value;
};

/** Ключ фильтров для зависимостей эффекта.
 *
 * Объект фильтров пересобирается на каждый рендер родителя, и поставь его в
 * зависимости useEffect как есть — поиск уходил бы на сервер по кругу. Строка
 * сравнивается по значению и меняется ровно тогда, когда меняется выбор.
 */
export const filtersKey = (filters) => {
    const value = normalizeFilters(filters);
    return [value.match, value.types.join(','), value.authors.join(',')].join('|');
};
