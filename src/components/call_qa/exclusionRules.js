/* «Исключить оценку» — правила без React (проверяет tests/ai_qa_exclusions.test.mjs).
 *
 * Пороги причины — зеркало call_qa/exclusions.py (REASON_MIN / REASON_MAX): окно
 * не должно пускать то, что сервер отвергнет, и наоборот. */

export const EXCLUSION_REASON_MIN = 3;
export const EXCLUSION_REASON_MAX = 500;

// Сколько категорий видно сразу; остальные — за «Ещё N». Больше десятка кнопок
// подряд — уже не выбор одним нажатием, а чтение списка.
export const CATEGORY_CHIPS = 8;

/* «Другое» — корзина для непонятных причин (её заводит ИИ). Выбрать её и ничего
 * не написать — обойти обязательную причину одним нажатием, поэтому с ней свои
 * слова обязательны. Зеркало OTHER_TITLES в call_qa/exclusions.py. */
const OTHER_TITLES = ['другое', 'прочее', 'иное', 'другая причина', 'прочие'];

export const isOtherCategory = (title) => OTHER_TITLES.includes(
    String(title || '').replace(/\s+/g, ' ').trim().replace(/[.;:!]+$/, '').toLowerCase());

/** Причина есть: категория выбрана (у «Другое» — ещё и свои слова) или своими
 * словами не короче порога. */
export const exclusionReady = ({ categoryId = null, categoryTitle = '', reason = '' } = {}) => {
    const words = String(reason || '').trim().length >= EXCLUSION_REASON_MIN;
    if (categoryId == null) return words;
    return isOtherCategory(categoryTitle) ? words : true;
};

/** Какие категории показать сейчас. Выбранная видна всегда — даже если она
 * дальше первых CATEGORY_CHIPS: иначе после «Ещё» и выбора её было бы не видно,
 * а заодно и того, что выбор сделан. */
export const visibleCategories = (list, { expanded = false, selectedId = null, limit = CATEGORY_CHIPS } = {}) => {
    const items = list || [];
    if (expanded || items.length <= limit) return { shown: items, hidden: 0 };
    const head = items.slice(0, limit);
    const picked = selectedId != null && !head.some((c) => c.id === selectedId)
        ? items.find((c) => c.id === selectedId) : null;
    const shown = picked ? [...head.slice(0, limit - 1), picked] : head;
    return { shown, hidden: items.length - shown.length };
};

/** Подпись причины одной строкой: «Ошибся номером · водитель звонил в такси» —
 * категория и свои слова; без категории — только слова. */
export const exclusionLabel = (exclusion) => {
    if (!exclusion) return '';
    const parts = [exclusion.category, String(exclusion.reason || '').trim()].filter(Boolean);
    return parts.join(' · ');
};

/** Кто и когда исключил: «Иванов Иван, 14.10.2026 12:30». */
export const exclusionAuthor = (exclusion) => (
    [exclusion?.excluded_by, exclusion?.excluded_at].filter(Boolean).join(', ')
);
