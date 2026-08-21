/* Списки разделов для выбора: живое отдельно от архива.
 *
 * /structure отдаёт архивные разделы и пространства всем, у кого есть право
 * управлять структурой. Так и задумано: вкладке «Структура» они нужны, и она
 * честно рисует их с бейджем «В архиве». Но по этому же ответу строятся все
 * остальные списки — дерево статей, выбор раздела статьи, раздела для правила
 * доступа, родительского раздела, — а фильтра по статусу в них не было.
 *
 * В выпадашке архивный раздел неотличим от живого: архивируют обычно как раз
 * дубль, у которого ровно то же имя. Отсюда и жалоба «разделы дублируются» —
 * человек видит две одинаковые строки и не знает, что вторая уже удалена.
 * Цена ошибки не косметическая: выбрав не ту строку, статью кладут в архивный
 * раздел, откуда её не видно в дереве.
 *
 * Текущее значение из списка НЕ выбрасывается, даже архивное: статья могла
 * попасть в архивный раздел, пока дубли были неразличимы, и пустое поле
 * вместо него скрыло бы этот факт от редактора вместо того, чтобы показать.
 */

/** Разделы, которые можно выбрать: без архива, но с текущим значением. */
export function selectableSections(sections, currentId = null) {
    const current = currentId === null || currentId === undefined ? '' : String(currentId);
    return (sections || []).filter(
        (s) => s.status !== 'archived' || String(s.id) === current,
    );
}

/** Подпись в списке. Архив помечен словами, а не молчанием. */
export function sectionOptionLabel(section, prefix = '') {
    const name = prefix ? `${prefix} · ${section.name}` : section.name;
    return section.status === 'archived' ? `${name} — в архиве` : name;
}

/* ── Дерево вместо плоского списка ──────────────────────────────────────────
 *
 * Внутри пространства имена разделов повторяются намеренно: у СЗоВ и у ОП есть
 * свои «Руководитель», «Супервайзер», «Оператор». В плоской выпадашке это шесть
 * строк, из которых три пары неразличимы, и статья регулярно уезжает не в ту
 * ветку. Отсюда две вещи ниже: путь до раздела в подписи и выбор по шагам.
 */

/* Ограничитель обхода — тот же, что в structureTree: сервер циклов не
   допускает, но зациклиться здесь значит подвесить редактор намертво. */
const MAX_DEPTH = 20;

/**
 * Родитель, ВИДИМЫЙ в этом же списке; иначе раздел сам становится корнем.
 *
 * Периметр человека часто начинается СЕРЕДИНОЙ дерева: руководителю СЗоВ видны
 * «Супервайзер» и «Оператор», а «Коммерческий директор» над всей веткой — нет.
 * Пока корнями считались только разделы с parent_section_id = null, такая ветка
 * не показывалась вовсе: в выпадашке раздела статьи оставался один «Общий
 * сотрудник», и человек не мог положить статью в СВОЙ раздел. Ровно это правило
 * уже держат дерево структуры (structureTree.parentKeyOf) и дерево витрины
 * (WikiLibrary) — здесь оно было потеряно.
 */
export function visibleParentId(section, visibleIds) {
    const parent = section?.parent_section_id || null;
    return parent && visibleIds.has(parent) ? parent : null;
}

/** Прямые потомки раздела (parentId = null — корни пространства). */
export function sectionChildren(sections, spaceId, parentId = null) {
    const list = selectableSections(sections);
    const shown = new Set(list.map((s) => s.id));
    const target = parentId || null;
    return list.filter(
        (s) => s.space_id === spaceId && visibleParentId(s, shown) === target,
    );
}

/**
 * Плоские строки дерева выбора: {section, depth, hasChildren, isOpen}.
 *
 * Строки идут в порядке обхода, свёрнутые ветки не разворачиваются. Живёт здесь,
 * а не в компоненте, по той же причине, что и остальное в этом файле: это чистая
 * функция над списком разделов, и «а что если родитель невидим» дешевле
 * проверить тестом (tests/wiki_section_picker.test.mjs), чем браузером.
 */
export function sectionTreeRows(sections, spaceId, expanded, parentId = null, depth = 0) {
    if (depth > MAX_DEPTH) return [];
    return sectionChildren(sections, spaceId, parentId).flatMap((section) => {
        const isOpen = !!expanded?.has(section.id);
        return [
            {
                section,
                depth,
                hasChildren: sectionChildren(sections, spaceId, section.id).length > 0,
                isOpen,
            },
            ...(isOpen ? sectionTreeRows(sections, spaceId, expanded, section.id, depth + 1) : []),
        ];
    });
}

/** Путь от корня до раздела включительно. Пустой массив, если раздел не найден. */
export function sectionAncestors(sections, sectionId) {
    const byId = new Map((sections || []).map((s) => [s.id, s]));
    const path = [];
    let current = byId.get(Number(sectionId));
    // Ограничитель на случай битого дерева: цикла быть не должно (сервер его не
    // допускает), но зациклиться здесь — значит подвесить вкладку намертво.
    let guard = 0;
    while (current && guard < 50) {
        path.unshift(current);
        current = current.parent_section_id ? byId.get(current.parent_section_id) : null;
        guard += 1;
    }
    return path;
}

/** Подпись с путём: «ОП › Супервайзер». Префикс — обычно название пространства. */
export function sectionPathLabel(sections, sectionId, prefix = '') {
    const path = sectionAncestors(sections, sectionId);
    if (!path.length) return prefix || '—';
    const name = path.map((s) => s.name).join(' › ');
    const label = prefix ? `${prefix} · ${name}` : name;
    return path[path.length - 1].status === 'archived' ? `${label} — в архиве` : label;
}
