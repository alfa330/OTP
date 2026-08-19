/* Дерево структуры вики: сборка, поиск, свёртка веток, перестановка соседей.
 *
 * Вынесено из WikiStructure.jsx по той же причине, что sectionPicker и
 * officeSchedule: это чистые функции над списком разделов, и проверять их
 * браузером ради каждого «а что если родитель в архиве» — дороже, чем
 * тестом (tests/wiki_structure_tree.test.mjs).
 *
 * Порядок разделов сервер отдаёт как ORDER BY space_id, position, id, а
 * вложенность считает клиент — поэтому и «выше/ниже» тоже считается здесь:
 * менять надо position У СОСЕДЕЙ ПО ВЕТКЕ, а не строку выше в списке.
 */

import {
    fixKeyboardLayout, foldKazakh, normalizeText, transliterateLatinToCyrillic,
} from './searchText.js';

/* Ограничитель обхода. Сервер циклов не допускает (родитель проверяется при
   сохранении), но битое дерево здесь означало бы не кривую картинку, а
   зависшую намертво вкладку — цена страховки в одну строку. */
const MAX_DEPTH = 20;

/** Живой раздел, поднятый в корень, если его родитель ушёл в архив. */
const parentKeyOf = (section, shown) => (
    (section.parent_section_id && shown.has(section.parent_section_id))
        ? section.parent_section_id
        : `root:${section.space_id}`
);

/**
 * Плоские строки дерева по пространствам: Map<space_id, [{section, depth, ...}]>.
 *
 * Строки идут в порядке обхода (родитель, затем его потомки) — на этом стоят и
 * свёртка, и фильтр: обе ходят по массиву, а не по вложенной структуре.
 *
 * childCount — прямые потомки (нужен, чтобы понять, есть ли что сворачивать),
 * descendants — вся ветка целиком (её размер показываем на свёрнутой строке,
 * иначе «+2» у раздела с двумя уровнями внутри вводило бы в заблуждение),
 * first/last — край ветки: по ним строка решает, показывать ли «переместить
 * выше/ниже». Считаются здесь, потому что здесь список соседей уже собран, —
 * искать их заново на каждую отрисовку строки значило бы обойти дерево ещё раз.
 */
export function buildSpaceTree(spaces, sections) {
    const alive = (sections || []).filter((s) => s.status !== 'archived');
    const shown = new Set(alive.map((s) => s.id));

    const children = new Map();
    alive.forEach((section) => {
        const key = parentKeyOf(section, shown);
        if (!children.has(key)) children.set(key, []);
        children.get(key).push(section);
    });

    const walk = (parentKey, depth) => {
        if (depth > MAX_DEPTH) return [];
        return (children.get(parentKey) || []).flatMap((section, i, siblings) => {
            const nested = walk(section.id, depth + 1);
            return [{
                section,
                depth,
                childCount: (children.get(section.id) || []).length,
                descendants: nested.length,
                first: i === 0,
                last: i === siblings.length - 1,
            }, ...nested];
        });
    };

    const tree = new Map();
    (spaces || []).forEach((space) => tree.set(space.id, walk(`root:${space.id}`, 0)));
    return tree;
}

/* ── Поиск ───────────────────────────────────────────────────────────────── */

const LATIN_ONLY = /^[a-z0-9\s-]+$/;

/**
 * Варианты запроса, по которым ищем в дереве.
 *
 * Полный queryVariants (поиск по статьям) здесь избыточен и вреден: он тянет
 * транслитерацию кириллицы в латиницу и алиасы марок машин — на названиях
 * разделов это только лишние совпадения. Нужны ровно два случая из практики:
 * «op» вместо «ОП» и забытая раскладка.
 */
export function structureNeedles(query) {
    const base = foldKazakh(normalizeText(query));
    if (!base) return [];
    const out = [base];
    const add = (value) => {
        const cleaned = foldKazakh(normalizeText(value));
        if (cleaned && !out.includes(cleaned)) out.push(cleaned);
    };
    if (LATIN_ONLY.test(base)) {
        add(transliterateLatinToCyrillic(base));
        // Раскладку чиним только на длинных запросах — ровно по той же причине,
        // что и в searchText.js: короткое «оп» флипается во что угодно.
        if (base.replace(/\s+/g, '').length >= 4) add(fixKeyboardLayout(base));
    }
    return out;
}

/** Строка, по которой ищем раздел: название, отдел ветки, владелец, пространство. */
export function sectionHaystack(section, spaceName = '') {
    return foldKazakh(normalizeText([
        section?.name, section?.department_name, section?.owner_name, spaceName,
    ].filter(Boolean).join(' ')));
}

/** Быстрые фильтры вкладки. Взаимоисключающие: «и публичный, и без доступа» —
 *  пустой ответ по определению, а не полезный срез. */
export const FOCUS_TESTS = {
    // Раздел без единого правила не видит никто, кроме администраторов.
    orphan: (s) => s.visibility_scope !== 'public' && !s.rules_count,
    public: (s) => s.visibility_scope === 'public',
    // can_grant_access считает сервер: потолок должности и граница отдела.
    grant: (s) => !!s.can_grant_access,
};

const matchesRow = (section, needles, spaceName, focus) => {
    if (focus && !FOCUS_TESTS[focus]?.(section)) return false;
    if (!needles.length) return true;
    const haystack = sectionHaystack(section, spaceName);
    return needles.some((needle) => haystack.includes(needle));
};

/**
 * Строки, которые остаются на экране при активном поиске/фильтре.
 *
 * Родителей найденного оставляем контекстом (context: true): без них найденный
 * «Оператор» повис бы в воздухе, а в структуре, где у СЗоВ и у ОП свои
 * одноимённые должности, именно ветка и отвечает на вопрос «который из двух».
 *
 * Идём с конца: к моменту встречи родителя все его потомки уже разобраны.
 */
export function filterRows(rows, { needles = [], focus = null, spaceName = '' } = {}) {
    const list = rows || [];
    if (!needles.length && !focus) {
        return list.map((row) => ({ ...row, matched: false, context: false }));
    }

    const keepChild = [];
    const out = new Array(list.length).fill(null);
    for (let i = list.length - 1; i >= 0; i -= 1) {
        const row = list[i];
        const own = matchesRow(row.section, needles, spaceName, focus);
        const child = !!keepChild[row.depth + 1];
        keepChild[row.depth + 1] = false;
        if (!own && !child) continue;
        keepChild[row.depth] = true;
        out[i] = { ...row, matched: own, context: !own };
    }
    return out.filter(Boolean);
}

/** Строки без потомков свёрнутых веток. */
export function collapseRows(rows, collapsed) {
    if (!collapsed || !collapsed.size) return rows || [];
    const out = [];
    let hideDeeperThan = null;
    (rows || []).forEach((row) => {
        if (hideDeeperThan !== null && row.depth > hideDeeperThan) return;
        hideDeeperThan = null;
        out.push(row);
        if (collapsed.has(row.section.id)) hideDeeperThan = row.depth;
    });
    return out;
}

/* ── Порядок ─────────────────────────────────────────────────────────────── */

const byPosition = (a, b) => (Number(a.position) || 0) - (Number(b.position) || 0)
    || (Number(a.id) || 0) - (Number(b.id) || 0);

/** Соседи раздела по ветке, в том же порядке, в каком они нарисованы. */
export function sectionSiblings(sections, section) {
    if (!section) return [];
    const alive = (sections || []).filter(
        (s) => s.status !== 'archived' && s.space_id === section.space_id);
    const shown = new Set(alive.map((s) => s.id));
    const key = parentKeyOf(section, shown);
    return alive.filter((s) => String(parentKeyOf(s, shown)) === String(key)).sort(byPosition);
}

/**
 * Что отправить на сервер, чтобы элемент переехал на шаг вверх (-1) или вниз (+1).
 *
 * Меняем местами ЗНАЧЕНИЯ position внутри ветки, а не нумеруем её заново:
 * position сквозной по пространству, и перенумерация ветки в 0..n-1 сдвинула бы
 * её относительно соседних веток. Заново нумеруем только если значения в ветке
 * совпали (так бывает после импорта) — тогда обмен ничего бы не изменил.
 *
 * Возвращает только те строки, у которых position реально меняется: обычно две.
 */
export function reorderPatches(items, id, direction) {
    const list = [...(items || [])];
    const index = list.findIndex((x) => String(x.id) === String(id));
    const target = index + direction;
    if (index < 0 || target < 0 || target >= list.length) return [];

    const moved = [...list];
    [moved[index], moved[target]] = [moved[target], moved[index]];

    const values = list.map((x) => Number(x.position) || 0).sort((a, b) => a - b);
    const distinct = values.every((value, i) => i === 0 || value > values[i - 1]);
    const targets = distinct ? values : values.map((_, i) => i);

    return moved
        .map((item, i) => ({ id: item.id, position: targets[i] }))
        .filter((patch, i) => (Number(moved[i].position) || 0) !== patch.position);
}

/* ── Подсветка найденного ────────────────────────────────────────────────── */

/**
 * Разбивка названия на куски с пометкой «сюда попал запрос».
 *
 * Свёртка и нижний регистр посимвольные, длина строки не меняется — поэтому
 * индексы найденного в свёрнутой копии годятся для исходной. Если регистр всё
 * же сместил длину (бывает у экзотических букв), подсветку не рисуем вовсе:
 * лучше без неё, чем со сдвигом на символ.
 */
export function highlightParts(text, needles) {
    const value = String(text || '');
    const plain = [{ text: value, hit: false }];
    if (!value || !needles?.length) return plain;

    const folded = foldKazakh(value).toLowerCase();
    if (folded.length !== value.length) return plain;

    const needle = needles.find((n) => n && folded.includes(n));
    if (!needle) return plain;

    const parts = [];
    let from = 0;
    for (;;) {
        const at = folded.indexOf(needle, from);
        if (at < 0) break;
        if (at > from) parts.push({ text: value.slice(from, at), hit: false });
        parts.push({ text: value.slice(at, at + needle.length), hit: true });
        from = at + needle.length;
    }
    if (from < value.length) parts.push({ text: value.slice(from), hit: false });
    return parts;
}
