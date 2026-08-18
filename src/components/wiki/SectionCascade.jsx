import React, { useMemo } from 'react';
import CustomSelect from '../ui/CustomSelect';
import { sectionAncestors, sectionChildren } from './sectionPicker';

/* Выбор раздела по шагам: пространство → ветка → ветка глубже.
 *
 * Плоский список здесь не работает по устройству самой структуры: у СЗоВ и у ОП
 * ветки называются одинаково («Руководитель», «Супервайзер», «Оператор»), и в
 * одной выпадашке три пары строк неразличимы. Статья от этого уезжает не в ту
 * ветку, а заметно это становится только когда её перестают видеть.
 *
 * Поэтому каждый уровень — свой список, и следующий появляется, только если у
 * выбранного раздела есть потомки. Выбор можно оборвать на любом уровне: статья
 * ляжет в тот раздел, который выбран последним, — «ОП» целиком тоже valid.
 */

const ROOT = '';

export default function SectionCascade({ sections, spaces, value, onChange, disabled = false }) {
    const liveSpaces = useMemo(
        () => (spaces || []).filter((sp) => sp.status !== 'archived'),
        [spaces],
    );

    // Путь до текущего выбора — он же состояние всех уровней. Отдельного стейта
    // нет намеренно: два источника правды разъезжаются, стоит родителю
    // смениться извне (например, статью открыли заново).
    const path = useMemo(() => sectionAncestors(sections, value), [sections, value]);
    const spaceId = path.length ? path[0].space_id : (liveSpaces[0]?.id ?? null);

    // Уровни: для каждого шага — что можно выбрать и что выбрано сейчас.
    const levels = useMemo(() => {
        if (!spaceId) return [];
        const result = [];
        let parentId = null;
        for (let depth = 0; depth <= path.length; depth += 1) {
            const options = sectionChildren(sections, spaceId, parentId);
            if (!options.length) break;
            result.push({
                options,
                selected: path[depth] ? String(path[depth].id) : ROOT,
                depth,
            });
            if (!path[depth]) break;
            parentId = path[depth].id;
        }
        return result;
    }, [sections, spaceId, path]);

    const pick = (depth, nextId) => {
        // Смена уровня обрезает всё, что было выбрано ниже: оставить «внука» от
        // прежней ветки нельзя — он к новой ветке никакого отношения не имеет.
        if (!nextId) {
            const parent = depth === 0 ? null : path[depth - 1];
            onChange(parent ? parent.id : null);
            return;
        }
        onChange(Number(nextId));
    };

    if (!liveSpaces.length) {
        return <div className="px-1 text-[12.5px] text-slate-400">Разделов пока нет</div>;
    }

    return (
        <div className="space-y-2">
            {liveSpaces.length > 1 && (
                <CustomSelect
                    variant="ios"
                    value={spaceId ? String(spaceId) : ''}
                    disabled={disabled}
                    onChange={(v) => {
                        // Смена пространства сбрасывает выбор целиком: раздел из
                        // прежнего пространства в новом не существует.
                        const first = sectionChildren(sections, Number(v), null)[0];
                        onChange(first ? first.id : null);
                    }}
                    options={liveSpaces.map((sp) => ({ value: String(sp.id), label: sp.name }))}
                    ariaLabel="Пространство"
                />
            )}

            {levels.map(({ options, selected, depth }) => (
                <CustomSelect
                    key={depth}
                    variant="ios"
                    value={selected}
                    disabled={disabled}
                    onChange={(v) => pick(depth, v)}
                    options={[
                        {
                            value: ROOT,
                            label: depth === 0 ? 'Выберите раздел' : '— оставить на уровне выше —',
                        },
                        ...options.map((s) => ({ value: String(s.id), label: s.name })),
                    ]}
                    searchable={options.length > 8}
                    ariaLabel={depth === 0 ? 'Раздел' : 'Подраздел'}
                />
            ))}

            {path.length > 0 && (
                <div className="px-1 text-[11.5px] text-slate-400">
                    Статья ляжет в: {path.map((s) => s.name).join(' › ')}
                </div>
            )}
        </div>
    );
}
