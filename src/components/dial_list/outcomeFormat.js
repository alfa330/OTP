/* Итоги звонка и их типы: подписи, сборка списка на сохранение, чипы журнала.
 *
 * Типы итога (запрос владельца 29.09.2026): «при выборе какого-то итога можно
 * было выбрать тип этого итога» — у «Отказа», например, «Дорого», «Уже работает
 * в другом парке», «Не интересно». Тип принадлежит одному итогу, своего цвета и
 * признака «перезвонить» у него нет — всё берётся у итога.
 *
 * Отдельный модуль без React — на него написан node-тест
 * (tests/dial_list_outcome_format.test.mjs). Здесь то, в чём нельзя разойтись:
 *   • подпись «Отказ · Дорого» — её же строит телефон, и в журнале, и в
 *     телефоне она обязана выглядеть одинаково;
 *   • сборка списка итогов для PUT и для «есть несохранённые изменения» — раньше
 *     перечень полей был переписан в трёх местах панели, и добавь типы только в
 *     два из них, кнопка «Сохранить» либо не загоралась, либо не гасла;
 *   • какие чипы типов показать под выбранным итогом в журнале.
 *
 * С 15.10.2026 у итога есть «нужна дата» (due_kind, автообзвон Тез): новое поле
 * итога обязано быть во всех трёх местах — serializeOutcomes, outcomesSnapshot и
 * validateOutcomes, — иначе повторится история с типами.
 */

/* Разделитель итога и типа: пробел, средняя точка U+00B7, пробел. */
export const SUBTYPE_SEP = ' · ';

/* Лимиты — те же, что проверяет сервер (dial_list/service.py). */
export const SUBTYPES_MAX = 20;
export const SUBTYPE_NAME_MAX = 64;

/* Значение фильтра журнала «Без типа»: у последней попытки итог есть, типа нет
   (попытки до появления типов и старые телефоны, которые тип не присылают). */
export const SUBTYPE_NONE = 'none';

/* Несохранённые строки редактора живут с временным id `new-…`: на сервер они
   уходят без id, и сервер их вставляет. */
export const isNewId = (id) => String(id ?? '').startsWith('new-');

/* Черновик — новая строка без названия (только что нажали «Добавить тип» и ещё не
   вписали): редактор сохраняет сам (20.11.2026), и такая строка не уходит на сервер и
   не держит сохранение остальных правок. Впишут название — уйдёт следующим сохранением. */
export const isDraftOutcome = (o) => isNewId(o?.id) && !String(o?.name ?? '').trim();
export const isDraftSubtype = (s) => isNewId(s?.id) && !String(s?.name ?? '').trim();

/* «Нужна дата» у итога (автообзвон Тез, контракт 15.10.2026): '' — нет;
   'callback' — «Перезвонить», оператор обязан указать дату и время перезвона;
   'online' — «Согласен выйти на линию», обязательна дата выхода. Водитель с таким
   итогом возвращается к СВОЕМУ оператору к сроку, поэтому итог с датой всегда
   «перезвонить» — сервер (save_outcomes) ставит requeue сам, и здесь так же, иначе
   снимок «сохранено» и рабочая копия разошлись бы на первом же сохранении.
   У удалённого КЦ дат нет: его итоги всегда уходят с ''. */
export const DUE_KINDS = ['', 'callback', 'online'];
export const DUE_KIND_OPTIONS = [
    { value: '', label: 'Нет' },
    { value: 'callback', label: 'Перезвон' },
    { value: 'online', label: 'Выход на линию' },
];

/** Вид даты итога: '' | 'callback' | 'online'; всё прочее — ''. */
export const normalizeDueKind = (value) => {
    const kind = String(value ?? '').trim().toLowerCase();
    return DUE_KINDS.includes(kind) ? kind : '';
};

/** Название типа из итога ({subtype: {name}}) или ''. */
export const outcomeSubtypeName = (outcome) => {
    const name = outcome?.subtype?.name;
    return typeof name === 'string' ? name.trim() : '';
};

/** «Отказ · Дорого», без типа — «Отказ»; нет итога — ''. */
export const outcomeLabel = (outcome) => {
    const name = typeof outcome?.name === 'string' ? outcome.name.trim() : '';
    if (!name) return '';
    const sub = outcomeSubtypeName(outcome);
    return sub ? `${name}${SUBTYPE_SEP}${sub}` : name;
};

const cleanName = (value) => String(value ?? '').trim().slice(0, SUBTYPE_NAME_MAX);

/** Копия списка итогов для редактора: у каждого итога свой массив типов.
    Старый сервер типов не присылает — тогда у всех пустой список. */
export const toEditable = (list) => (Array.isArray(list) ? list : []).map((o) => ({
    ...o,
    subtypes: (Array.isArray(o?.subtypes) ? o.subtypes : []).map((s) => ({ ...s })),
}));

/**
 * Умеет ли сервер типы. Новый присылает subtypes у каждого итога (хотя бы
 * пустой), старый — нет, а его PUT молча выбросил бы типы: руководитель увидел
 * бы «сохранено», и все типы пропали бы. Поэтому у старого сервера раскрывашку
 * типов не показываем. Пустой список ничего не говорит — считаем, что умеет
 * (редактор всё равно сразу получает стартовый набор итогов).
 */
export const serverHasSubtypes = (list) => {
    const arr = Array.isArray(list) ? list : [];
    return arr.length === 0 || arr.some((o) => Array.isArray(o?.subtypes));
};

/**
 * Список итогов в том виде, в каком он уходит на сервер (PUT …/outcomes).
 * Он же — снимок для «есть несохранённые изменения»: снимок сохранённого и
 * снимок рабочей копии сравниваются строкой, и поле, забытое в одном из них,
 * дало бы вечное «изменено». Типы уходят ВСЕГДА и целиком: у сервера список
 * полный, и тип, которого в нём нет, выключается.
 *
 * due_kind («нужна дата», 15.10.2026) — только у автообзвона Тез ({ dueKinds: true }),
 * и там всегда, '' тоже: ключа нет — сервер дату итога не трогает, и снятая в редакторе
 * дата осталась бы. Итог с датой всегда «перезвонить» — см. DUE_KINDS. У удалённого КЦ
 * ключа нет вовсе: его запрос — прежний.
 */
export const serializeOutcomes = (items, { dueKinds = false } = {}) => (Array.isArray(items) ? items : [])
    .filter((o) => !isDraftOutcome(o))
    .map((o) => {
        const dueKind = dueKinds ? normalizeDueKind(o.due_kind) : '';
        return {
            id: isNewId(o.id) ? undefined : o.id,
            name: String(o.name ?? '').trim(),
            color: o.color,
            requeue: !!o.requeue || dueKind !== '',
            is_active: o.is_active !== false,
            ...(dueKinds ? { due_kind: dueKind } : {}),
            subtypes: (Array.isArray(o.subtypes) ? o.subtypes : []).filter((s) => !isDraftSubtype(s)).map((s) => ({
                id: isNewId(s.id) ? undefined : s.id,
                name: cleanName(s.name),
                is_active: s.is_active !== false,
            })),
        };
    });

/** Строка-снимок для сравнения «сохранено / в редакторе». Собирается тем же
    serializeOutcomes с теми же опциями, поэтому новое поле итога (due_kind) попадает и сюда. */
export const outcomesSnapshot = (items, options) => JSON.stringify(serializeOutcomes(items, options));

/**
 * Проверка перед PUT — те же правила, что у сервера, чтобы руководитель видел
 * ошибку сразу, а не после запроса. Возвращает текст ошибки или ''.
 */
export const validateOutcomes = (payload) => {
    for (const o of payload) {
        if (!o.name) return 'У каждого итога должно быть название';
    }
    const seenOutcomes = new Set();
    for (const o of payload) {
        const key = o.name.toLowerCase();
        if (seenOutcomes.has(key)) return `Итог «${o.name}» повторяется`;
        seenOutcomes.add(key);
    }
    if (payload.length && !payload.some((o) => o.is_active !== false)) return 'Хотя бы один итог должен быть включён';
    for (const o of payload) {
        // Свёрнутое serializeOutcomes значение всегда допустимо; проверка — для
        // списка, собранного мимо него (а сервер ответил бы 400 уже после запроса).
        if (o.due_kind !== undefined && !DUE_KINDS.includes(o.due_kind)) {
            return `У итога «${o.name}» неизвестный вид даты: «${o.due_kind}»`;
        }
    }
    for (const o of payload) {
        const subs = o.subtypes || [];
        if (subs.length > SUBTYPES_MAX) return `У итога «${o.name}» не больше ${SUBTYPES_MAX} типов`;
        const seen = new Set();
        for (const s of subs) {
            if (!s.name) return `У итога «${o.name}» у каждого типа должно быть название`;
            const key = s.name.toLowerCase();
            if (seen.has(key)) return `Тип «${s.name}» у итога «${o.name}» повторяется`;
            seen.add(key);
        }
    }
    return '';
};

/**
 * После сохранения: какие временные id (`new-…`) получили настоящие. sent — строки
 * редактора в том виде, в каком ушли на сервер (по ним и ищем), server — список из
 * ответа. Итог узнаётся по названию (сервер не даёт двух итогов с одним названием без
 * учёта регистра), тип — по названию внутри своего итога. Ответ — { временный: настоящий }.
 *
 * Редактор сохраняет сам (20.11.2026), и пока ответ шёл, оператор мог продолжить
 * печатать: рабочую копию ответом не заменяем, а только переписываем в ней id — иначе
 * набранное после отправки пропало бы.
 */
export const savedIdMap = (sent, server) => {
    const map = {};
    const byName = new Map();
    for (const o of Array.isArray(server) ? server : []) {
        byName.set(String(o?.name ?? '').trim().toLowerCase(), o);
    }
    for (const o of Array.isArray(sent) ? sent : []) {
        if (isDraftOutcome(o)) continue;
        const match = byName.get(String(o.name ?? '').trim().toLowerCase());
        if (!match) continue;
        if (isNewId(o.id)) map[o.id] = match.id;
        const subs = new Map((Array.isArray(match.subtypes) ? match.subtypes : [])
            .map((s) => [cleanName(s?.name).toLowerCase(), s]));
        for (const s of Array.isArray(o.subtypes) ? o.subtypes : []) {
            if (!isNewId(s.id) || isDraftSubtype(s)) continue;
            const sm = subs.get(cleanName(s.name).toLowerCase());
            if (sm) map[s.id] = sm.id;
        }
    }
    return map;
};

/** Рабочая копия с настоящими id вместо временных (savedIdMap). */
export const applyIdMap = (items, idMap) => (Array.isArray(items) ? items : []).map((o) => ({
    ...o,
    id: idMap[o.id] || o.id,
    subtypes: (Array.isArray(o.subtypes) ? o.subtypes : []).map((s) => (idMap[s.id] ? { ...s, id: idMap[s.id] } : s)),
}));

/** Сколько включённых типов у итога. */
export const activeSubtypeCount = (outcome) => (Array.isArray(outcome?.subtypes) ? outcome.subtypes : [])
    .filter((s) => s.is_active !== false).length;

/**
 * Подпись кнопки-раскрывашки под итогом: «Типы · 3»; включённых нет, но
 * выключенные есть — «Типы»; типов нет вовсе — «Добавить типы» (или ничего,
 * если править нельзя: раскрывать пустой список незачем).
 */
export const subtypesToggleLabel = (outcome, canEdit = true) => {
    const all = Array.isArray(outcome?.subtypes) ? outcome.subtypes.length : 0;
    const active = activeSubtypeCount(outcome);
    if (active > 0) return `Типы${SUBTYPE_SEP}${active}`;
    if (all > 0) return 'Типы';
    return canEdit ? 'Добавить типы' : '';
};

/**
 * После сохранения несохранённые итоги получают настоящие id, и раскрытый
 * список типов у нового итога схлопнулся бы. Раскрытость переносим по
 * названию: сервер не даёт двух итогов с одним названием (без учёта регистра).
 */
export const remapExpanded = (prevItems, expanded, nextItems) => {
    const names = new Set((Array.isArray(prevItems) ? prevItems : [])
        .filter((o) => expanded?.[o.id])
        .map((o) => String(o.name ?? '').trim().toLowerCase()));
    const out = {};
    for (const o of Array.isArray(nextItems) ? nextItems : []) {
        if (names.has(String(o.name ?? '').trim().toLowerCase())) out[o.id] = true;
    }
    return out;
};

/**
 * Чипы типов под выбранным итогом в журнале. entry — элемент by_outcome
 * ({subtypes: [{id, name, is_active, count}], none_count}), selected — текущий
 * фильтр типа ('' | uuid | 'none').
 *
 * Правило то же, что у чипов итогов: тип с нулём не показываем (нажатие дало
 * бы пустой список), выбранный остаётся, чтобы его можно было снять. «Без типа»
 * — отдельный чип, иначе числа типов не сходятся с числом на итоге, и кажется,
 * что строки потерялись. У итога без типов вовсе второй ряд не нужен: «Без
 * типа» там совпадал бы с самим итогом.
 */
export const subtypeChips = (entry, selected = '') => {
    const subs = Array.isArray(entry?.subtypes) ? entry.subtypes : [];
    if (!subs.length) return [];
    const chips = subs
        .filter((s) => (Number(s.count) || 0) > 0 || s.id === selected)
        .map((s) => ({ id: s.id, name: s.name, count: Number(s.count) || 0, is_active: s.is_active !== false }));
    const none = Number(entry?.none_count) || 0;
    if (none > 0 || selected === SUBTYPE_NONE) {
        chips.push({ id: SUBTYPE_NONE, name: 'Без типа', count: none, is_active: true, none: true });
    }
    return chips;
};
