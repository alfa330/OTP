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

/** Прямые потомки раздела (parentId = null — корни пространства). */
export function sectionChildren(sections, spaceId, parentId = null) {
    return selectableSections(sections).filter(
        (s) => s.space_id === spaceId
            && (s.parent_section_id || null) === (parentId || null),
    );
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
