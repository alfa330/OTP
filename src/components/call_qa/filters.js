/* Фильтры раздела «ИИ-оценка» — одно место вместо литералов в трёх компонентах.
 *
 * Панель фильтров, запросы обоих списков и подбор «оценить из отбора» обязаны
 * понимать набор ОДИНАКОВО: пока правила жили внутри компонентов, вкладка
 * «Очередь ревью» и вкладка «Звонки» неизбежно разошлись бы, и один и тот же
 * отбор давал бы на них разные выборки.
 *
 * Модуль намеренно чистый (никакого React и axios): его поведение проверяется
 * node-тестом, а не через отрисовку раздела.
 *
 * Зеркало серверной проверки call_qa.api.normalise_list_filters. Пустое
 * значение здесь и там значит «фильтра нет», а не «показать пусто».
 */

/** Ничего не отобрано. Именно этот объект — «чистое» состояние панели. */
export const EMPTY_FILTERS = {
    date_from: '',
    date_to: '',
    direction_id: null,
    group_id: null,
    operator_id: null,
    reviewed: '',      // '' | 'yes' | 'no' — есть ли оценка человека
    score_min: '',
    score_max: '',
    q: '',
};

/* Группа «без группы» — не пустой фильтр, а отдельная корзина: у части звонков
 * из АТС учётной записи нет вовсе (осталось имя из телефонии), и без этого
 * пункта такие строки просто пропадали бы из любого разреза по группам. Значение
 * строковое, потому что id у корзины быть не может. */
export const NO_GROUP = 'none';

const isSet = (value) => value !== null && value !== undefined && value !== '';

/** Сколько фильтров отобрано — число на кнопке «Фильтры». Период считается ОДНИМ. */
export const countActiveFilters = (filters) => {
    const f = filters || EMPTY_FILTERS;
    let count = 0;
    if (isSet(f.date_from) || isSet(f.date_to)) count += 1;
    if (isSet(f.direction_id)) count += 1;
    if (isSet(f.group_id)) count += 1;
    if (isSet(f.operator_id)) count += 1;
    if (isSet(f.reviewed)) count += 1;
    if (isSet(f.score_min) || isSet(f.score_max)) count += 1;
    if (isSet(f.q)) count += 1;
    return count;
};

export const hasActiveFilters = (filters) => countActiveFilters(filters) > 0;

/** Параметры запроса. Пустые НЕ отправляем: сервер отличает «нет фильтра» от значения. */
export const filtersToParams = (filters) => {
    const f = filters || EMPTY_FILTERS;
    const params = {};
    if (isSet(f.date_from)) params.date_from = f.date_from;
    if (isSet(f.date_to)) params.date_to = f.date_to;
    if (isSet(f.direction_id)) params.direction_id = f.direction_id;
    if (isSet(f.group_id)) params.group_id = f.group_id;
    if (isSet(f.operator_id)) params.operator_id = f.operator_id;
    if (isSet(f.reviewed)) params.reviewed = f.reviewed;
    if (isSet(f.score_min)) params.score_min = f.score_min;
    if (isSet(f.score_max)) params.score_max = f.score_max;
    if (isSet(f.q)) params.q = String(f.q).trim();
    return params;
};

/* Ключ отбора для зависимостей эффекта. Объект фильтров пересоздаётся на каждом
 * рендере, и класть его в deps — это бесконечный перезапрос (та же болезнь, что
 * у нестабильных колбэков в зависимостях). Строка сравнивается по значению. */
export const filtersKey = (filters) => JSON.stringify(filtersToParams(filters));

/** Балл ИИ: пустое поле — «без границы», иначе целое 0…100. */
export const clampScore = (value) => {
    const text = String(value ?? '').trim();
    if (!text) return '';
    const parsed = Math.round(Number(text));
    if (!Number.isFinite(parsed)) return '';
    return String(Math.max(0, Math.min(100, parsed)));
};

const shortDate = (iso) => {
    const parts = String(iso || '').split('-');
    return parts.length === 3 ? `${parts[2]}.${parts[1]}` : iso;
};

/* Имя фильтра перед значением («Группа · Ночная смена») помогает читать чип, но
 * только пока не дублирует само значение: у групп в портале названия вида
 * «Группа Тестбаевой», и выходило «Группа Группа Тестбаевой». Если значение уже
 * начинается с имени фильтра — имя опускаем. */
const chipName = (name, label) => (
    String(label).toLowerCase().startsWith(String(name).toLowerCase()) ? '' : name
);

/** Подписи отобранного — чипы под кнопкой. `clear` снимает ОДИН фильтр. */
export const activeFilterChips = (filters, options = {}) => {
    const f = filters || EMPTY_FILTERS;
    const { directions = [], groups = [], operators = [] } = options;
    const nameOf = (list, id) => (list.find((item) => String(item.id) === String(id)) || {}).name;
    const chips = [];
    const patch = (fields) => ({ ...f, ...fields });
    const add = (key, name, label, next) => chips.push(
        { key, name: chipName(name, label), label, next });

    if (isSet(f.date_from) || isSet(f.date_to)) {
        const label = isSet(f.date_from) && isSet(f.date_to)
            ? (f.date_from === f.date_to ? shortDate(f.date_from)
                : `${shortDate(f.date_from)} — ${shortDate(f.date_to)}`)
            : (isSet(f.date_from) ? `с ${shortDate(f.date_from)}` : `по ${shortDate(f.date_to)}`);
        add('period', 'Период', label, patch({ date_from: '', date_to: '' }));
    }
    if (isSet(f.direction_id)) {
        add('direction', 'Направление',
            nameOf(directions, f.direction_id) || `#${f.direction_id}`,
            patch({ direction_id: null }));
    }
    if (isSet(f.group_id)) {
        add('group', 'Группа',
            f.group_id === NO_GROUP
                ? 'без группы' : (nameOf(groups, f.group_id) || `#${f.group_id}`),
            patch({ group_id: null }));
    }
    if (isSet(f.operator_id)) {
        add('operator', 'Сотрудник',
            nameOf(operators, f.operator_id) || `#${f.operator_id}`,
            patch({ operator_id: null }));
    }
    if (isSet(f.reviewed)) {
        add('reviewed', 'Оценка человека', f.reviewed === 'yes' ? 'есть' : 'нет',
            patch({ reviewed: '' }));
    }
    if (isSet(f.score_min) || isSet(f.score_max)) {
        const label = isSet(f.score_min) && isSet(f.score_max)
            ? `${f.score_min}—${f.score_max}`
            : (isSet(f.score_min) ? `от ${f.score_min}` : `до ${f.score_max}`);
        add('score', 'Балл ИИ', label, patch({ score_min: '', score_max: '' }));
    }
    if (isSet(f.q)) {
        add('q', 'Поиск', f.q, patch({ q: '' }));
    }
    return chips;
};

/* Что из отбора принимает подтяжка «Из АТС».
 *
 * Сотрудник и период уходят в саму АТС — это её параметры выборки. Направление и
 * группа в АТС не уходят (она про них не знает), но сужают КРУГ ЛЮДЕЙ, среди
 * которых портал ищет, чей звонок подтянуть: без них выбранная в панели группа
 * молча игнорировалась и портал шёл за звонком случайного человека всего отдела.
 *
 * Балл ИИ и наличие оценки человека не идут вовсе: у ещё не подтянутого звонка
 * ни того, ни другого не существует. Поиск по имени — тоже: сотрудника здесь
 * выбирают селектором. */
export const pullParamsFromFilters = (filters) => {
    const f = filters || EMPTY_FILTERS;
    const params = {};
    if (isSet(f.operator_id)) params.operator_id = f.operator_id;
    if (isSet(f.date_from)) params.date_from = f.date_from;
    if (isSet(f.date_to)) params.date_to = f.date_to;
    if (isSet(f.direction_id)) params.direction_id = f.direction_id;
    if (isSet(f.group_id)) params.group_id = f.group_id;
    return params;
};

/** Люди, подходящие под уже выбранные направление и группу — для селектора и подтяжки. */
export const operatorsMatching = (operators, filters) => {
    const f = filters || EMPTY_FILTERS;
    return (operators || []).filter((person) => {
        if (isSet(f.direction_id)
            && String(person.direction_id) !== String(f.direction_id)) return false;
        if (isSet(f.group_id)) {
            if (f.group_id === NO_GROUP) return person.group_id == null;
            if (String(person.group_id) !== String(f.group_id)) return false;
        }
        return true;
    });
};
