import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { selectableSections, sectionAncestors, sectionPathLabel } from './sectionPicker';

/* Выбор раздела деревом: родитель раскрывается, а не лежит в общем списке.
 *
 * Плоский список здесь не работает по устройству самой структуры: у СЗоВ и у ОП
 * ветки называются одинаково — «Руководитель группы», «Супервайзер», «Оператор».
 * В одной выпадашке это шесть строк, из которых три пары неразличимы, и статья
 * уезжает не в ту ветку; заметно это становится только когда её перестают
 * видеть нужные люди.
 *
 * Поведение: нажатие на родителя выбирает его И раскрывает, список остаётся
 * открытым — «положить в ОП целиком» тоже допустимо. Нажатие на конечную ветку
 * выбирает и закрывает. Шеврон раскрывает, ничего не выбирая.
 *
 * Позиционирование и закрытие повторяют CustomSelect (портал + fixed): список
 * обязан переживать overflow модалки редактора, а скролл внутри него самого не
 * должен его закрывать.
 */

const rowsOf = (sections, spaceId, expanded, depth = 0, parentId = null) =>
    selectableSections(sections)
        .filter((s) => s.space_id === spaceId && (s.parent_section_id || null) === parentId)
        .flatMap((section) => {
            const children = selectableSections(sections).filter(
                (x) => x.space_id === spaceId && x.parent_section_id === section.id,
            );
            const isOpen = expanded.has(section.id);
            return [
                { section, depth, hasChildren: children.length > 0, isOpen },
                ...(isOpen ? rowsOf(sections, spaceId, expanded, depth + 1, section.id) : []),
            ];
        });

export default function SectionTreeSelect({
    sections = [], spaces = [], value, onChange, disabled = false,
}) {
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState(null);
    const [query, setQuery] = useState('');
    const [expanded, setExpanded] = useState(() => new Set());
    const btnRef = useRef(null);
    const popRef = useRef(null);
    const searchRef = useRef(null);

    const liveSpaces = useMemo(
        () => (spaces || []).filter((sp) => sp.status !== 'archived'), [spaces]);

    const path = useMemo(() => sectionAncestors(sections, value), [sections, value]);

    // Ветка выбранного раздела раскрыта всегда: открыв список, человек обязан
    // увидеть, где стоит текущий выбор, а не искать его по свёрнутым узлам.
    useEffect(() => {
        if (!path.length) return;
        setExpanded((prev) => {
            const next = new Set(prev);
            path.slice(0, -1).forEach((s) => next.add(s.id));
            return next;
        });
    }, [value]);   // eslint-disable-line react-hooks/exhaustive-deps

    const recompute = () => {
        const el = btnRef.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        const spaceBelow = window.innerHeight - r.bottom;
        const openUp = spaceBelow < 260 && r.top > spaceBelow;
        setCoords({
            left: Math.round(r.left),
            width: Math.round(r.width),
            top: openUp ? undefined : Math.round(r.bottom + 4),
            bottom: openUp ? Math.round(window.innerHeight - r.top + 4) : undefined,
            maxHeight: Math.max(200, Math.round((openUp ? r.top : spaceBelow) - 16)),
        });
    };

    useLayoutEffect(() => { if (open) recompute(); }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        if (!open) { setQuery(''); return undefined; }
        const id = requestAnimationFrame(() => searchRef.current?.focus());
        return () => cancelAnimationFrame(id);
    }, [open]);

    useEffect(() => {
        if (!open) return undefined;
        const onDoc = (e) => {
            if (btnRef.current?.contains(e.target) || popRef.current?.contains(e.target)) return;
            setOpen(false);
        };
        const onScroll = (e) => {
            if (popRef.current && (popRef.current === e.target || popRef.current.contains(e.target))) return;
            recompute();
        };
        const onKey = (e) => {
            if (e.key !== 'Escape') return;
            setOpen(false);
            requestAnimationFrame(() => btnRef.current?.focus());
        };
        document.addEventListener('mousedown', onDoc);
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', recompute);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDoc);
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', recompute);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);

    // Поиск разворачивает дерево в плоский список, но подпись несёт весь путь —
    // иначе в результатах снова окажутся три неразличимых «Супервайзера».
    const searchHits = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return null;
        return selectableSections(sections)
            .filter((s) => sectionPathLabel(sections, s.id).toLowerCase().includes(q))
            .slice(0, 40);
    }, [sections, query]);

    const toggle = (id) => setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });

    const choose = (section, hasChildren) => {
        onChange?.(section.id);
        if (!hasChildren) {
            setOpen(false);
            requestAnimationFrame(() => btnRef.current?.focus());
            return;
        }
        // У родителя нажатие ещё и раскрывает: чаще всего цель — ветка внутри.
        setExpanded((prev) => new Set(prev).add(section.id));
    };

    const label = path.length
        ? path.map((s) => s.name).join(' › ')
        : 'Выберите…';

    return (
        <div>
            <button
                ref={btnRef}
                type="button"
                disabled={disabled}
                aria-haspopup="tree"
                aria-expanded={open}
                aria-label="Раздел статьи"
                onClick={() => { if (!disabled) setOpen((v) => !v); }}
                className={`flex w-full items-center justify-between gap-2 rounded-xl bg-white px-3 py-2 text-left text-[12.5px] font-medium ring-1 transition-all ${
                    disabled
                        ? 'cursor-not-allowed text-slate-400 ring-slate-200/70'
                        : 'text-slate-700 ring-slate-200 hover:ring-slate-300'
                }`}
            >
                <span className={`truncate ${path.length ? '' : 'text-slate-400'}`}>{label}</span>
                <ChevronDown size={14} className={`shrink-0 text-slate-400 transition ${open ? 'rotate-180' : ''}`} />
            </button>

            {open && coords && createPortal(
                <div
                    ref={popRef}
                    style={{
                        position: 'fixed',
                        left: coords.left,
                        width: coords.width,
                        top: coords.top,
                        bottom: coords.bottom,
                        maxHeight: coords.maxHeight,
                        zIndex: 9999,
                    }}
                    className="flex flex-col overflow-hidden rounded-xl bg-white shadow-xl ring-1 ring-slate-200"
                >
                    <div className="border-b border-slate-100 p-2">
                        <input
                            ref={searchRef}
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="Поиск раздела…"
                            className="w-full rounded-lg bg-slate-50 px-2.5 py-1.5 text-[12.5px] text-slate-700 outline-none ring-1 ring-transparent focus:ring-blue-200"
                        />
                    </div>

                    <div className="min-h-0 flex-1 overflow-y-auto py-1">
                        {searchHits ? (
                            searchHits.length === 0 ? (
                                <div className="px-3 py-6 text-center text-[12.5px] text-slate-400">
                                    Ничего не нашлось
                                </div>
                            ) : searchHits.map((section) => (
                                <button
                                    key={section.id}
                                    type="button"
                                    onClick={() => {
                                        onChange?.(section.id);
                                        setOpen(false);
                                    }}
                                    className={`block w-full px-3 py-2 text-left text-[12.5px] transition hover:bg-slate-50 ${
                                        String(section.id) === String(value) ? 'text-blue-600' : 'text-slate-700'
                                    }`}
                                >
                                    {sectionPathLabel(sections, section.id)}
                                </button>
                            ))
                        ) : liveSpaces.map((space) => {
                            const rows = rowsOf(sections, space.id, expanded);
                            if (!rows.length) return null;
                            return (
                                <div key={space.id}>
                                    {liveSpaces.length > 1 && (
                                        <div className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                                            {space.name}
                                        </div>
                                    )}
                                    {rows.map(({ section, depth, hasChildren, isOpen }) => (
                                        <div
                                            key={section.id}
                                            className={`flex items-center gap-1 pr-2 transition hover:bg-slate-50 ${
                                                String(section.id) === String(value) ? 'bg-blue-50/60' : ''
                                            }`}
                                            style={{ paddingLeft: `${8 + depth * 16}px` }}
                                        >
                                            {hasChildren ? (
                                                <button
                                                    type="button"
                                                    // Шеврон раскрывает, НИЧЕГО не выбирая: иначе нельзя
                                                    // заглянуть в ветку, не сменив текущий выбор.
                                                    onClick={(e) => { e.stopPropagation(); toggle(section.id); }}
                                                    className="grid h-6 w-6 shrink-0 place-items-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                                                    aria-label={isOpen ? 'Свернуть' : 'Раскрыть'}
                                                >
                                                    {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                                                </button>
                                            ) : (
                                                <span className="h-6 w-6 shrink-0" />
                                            )}
                                            <button
                                                type="button"
                                                onClick={() => choose(section, hasChildren)}
                                                className={`min-w-0 flex-1 truncate py-1.5 text-left text-[12.5px] ${
                                                    String(section.id) === String(value)
                                                        ? 'font-medium text-blue-600'
                                                        : 'text-slate-700'
                                                }`}
                                            >
                                                {section.name}
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            );
                        })}
                    </div>
                </div>,
                document.body,
            )}

            {path.length > 0 && (
                <div className="mt-1 px-1 text-[11.5px] text-slate-400">
                    Статья ляжет в: {path.map((s) => s.name).join(' › ')}
                </div>
            )}
        </div>
    );
}
