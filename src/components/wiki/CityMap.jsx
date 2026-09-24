import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Maximize2, Minus, Plus } from 'lucide-react';
import {
    FULL_VIEW, KAZAKHSTAN_PATH, LAKES_PATH, MAP_HEIGHT, MAP_WIDTH, NEIGHBORS_PATH,
    RIVERS_PATH, cityPoint, clampView, fitView, viewHeight, zoomView,
} from './kazakhstanMap';
import {
    NO_ZONE_COLOR, cityGenitive, cityZone, zoneHubs, zonePalette,
} from './cityRules';

/* Схема городов по макету постановки #322 — в духе «Карт» Apple.
 *
 * Схема читается картой, а не силуэтом (замечание владельца 24.09.2026:
 * «карта выглядит слишком простой»): вода фоном — Каспий получается сам,
 * соседи серой сушей, страна белым листом с мягкой тенью, поверх — озёра и
 * крупные реки. Точка города — цвет зоны обслуживающего офиса в белом кольце;
 * у города со своим офисом точка крупнее и с белой серединкой.
 *
 * Как в «Картах»: кнопки «+ / −» в стеклянной плашке, двойной щелчок
 * приближает, приближённую схему можно тащить; щипок на трекпаде (⌘/Ctrl +
 * колёсико) и двумя пальцами на телефоне. Простое колёсико НЕ перехватываем —
 * им листают страницу, и схема на пути прокрутки не должна её останавливать.
 *
 * Размеры точек и подписей заданы в ПИКСЕЛЯХ экрана и пересчитываются в
 * единицы схемы на каждый вид: при приближении точки не раздуваются, а
 * расходятся — так и разводятся Караганда с Темиртау и Туркестан с Кентау.
 *
 * Легенда зон — фильтр: нажатие подсвечивает города зоны, гасит остальные и
 * приближает схему к зоне; повторное — возвращает всю страну.
 *
 * compact — телефон: схема узкая, поэтому подписи есть только у городов с
 * офисом, пока схему не приблизили, а легенда стоит под схемой, а не поверх.
 */

const ASPECT = MAP_HEIGHT / MAP_WIDTH;
const DRAG_SLOP = 4;          // px: меньше — это нажатие, а не перетаскивание
const ANIMATION_MS = 240;

const easeOut = (t) => 1 - (1 - t) ** 3;

const pluralTariffs = (count) => {
    const n = Math.abs(count) % 100;
    const tail = n % 10;
    if (n > 10 && n < 20) return `${count} тарифов`;
    if (tail === 1) return `${count} тариф`;
    if (tail >= 2 && tail <= 4) return `${count} тарифа`;
    return `${count} тарифов`;
};

const labelPosition = (side, x, y, gap, size) => {
    switch (side) {
        case 'left': return { x: x - gap, y: y + size * 0.35, textAnchor: 'end' };
        case 'top': return { x, y: y - gap, textAnchor: 'middle' };
        case 'bottom': return { x, y: y + gap + size * 0.75, textAnchor: 'middle' };
        default: return { x: x + gap, y: y + size * 0.35, textAnchor: 'start' };
    }
};

/* Стеклянная плашка — общий вид кнопок и легенды поверх схемы. */
const glass = 'bg-white/85 ring-1 ring-slate-900/[0.06] shadow-[0_1px_3px_rgba(15,23,42,0.10),0_6px_16px_-8px_rgba(15,23,42,0.18)] backdrop-blur-md';

const controlButton = 'grid place-items-center text-slate-600 transition '
    + 'hover:bg-slate-900/[0.05] hover:text-slate-900 active:scale-95 disabled:opacity-30 '
    + 'disabled:hover:bg-transparent';

export default function CityMap({ cities, selectedId, onSelect, compact = false }) {
    const uid = useId().replace(/:/g, '');
    const wrapRef = useRef(null);
    const svgRef = useRef(null);
    const [width, setWidth] = useState(compact ? 360 : 900);
    const [view, setView] = useState(FULL_VIEW);
    const [hoverId, setHoverId] = useState(null);
    const [focusZone, setFocusZone] = useState('');
    const [dragging, setDragging] = useState(false);
    const viewRef = useRef(view);
    const frameRef = useRef(0);
    const gestureRef = useRef({ pointers: new Map(), moved: false, start: null });

    viewRef.current = view;

    // Ширина схемы на экране — от неё считаются размеры точек в пикселях.
    useEffect(() => {
        const node = wrapRef.current;
        if (!node || typeof ResizeObserver === 'undefined') return undefined;
        const observer = new ResizeObserver(([entry]) => {
            const next = Math.round(entry.contentRect.width);
            if (next > 0) setWidth(next);
        });
        observer.observe(node);
        return () => observer.disconnect();
    }, []);

    useEffect(() => () => cancelAnimationFrame(frameRef.current), []);

    const animateTo = useCallback((target) => {
        cancelAnimationFrame(frameRef.current);
        const from = viewRef.current;
        const to = clampView(target);
        const started = performance.now();
        const step = (now) => {
            const t = Math.min(1, (now - started) / ANIMATION_MS);
            const k = easeOut(t);
            setView({
                x: from.x + (to.x - from.x) * k,
                y: from.y + (to.y - from.y) * k,
                w: from.w + (to.w - from.w) * k,
            });
            if (t < 1) frameRef.current = requestAnimationFrame(step);
        };
        frameRef.current = requestAnimationFrame(step);
    }, []);

    const palette = useMemo(() => zonePalette(cities), [cities]);
    const hubs = useMemo(() => zoneHubs(cities), [cities]);
    const zones = useMemo(() => Object.keys(palette).sort(new Intl.Collator('ru').compare), [palette]);

    // Зону могли снять в редакторе, пока она была подсвечена.
    useEffect(() => {
        if (focusZone && !zones.includes(focusZone)) setFocusZone('');
    }, [zones, focusZone]);

    const points = useMemo(() => (cities || [])
        .map((city) => ({ city, point: cityPoint(city.name), zone: cityZone(city, hubs) }))
        .filter((item) => item.point), [cities, hubs]);

    /* Пересчёт пикселей экрана в единицы схемы для ТЕКУЩЕГО вида. */
    const unit = view.w / Math.max(width, 1);
    const zoomed = view.w < MAP_WIDTH - 0.5;
    const deepZoom = view.w < MAP_WIDTH * 0.6;

    const toSvgPoint = (clientX, clientY) => {
        const rect = svgRef.current?.getBoundingClientRect();
        if (!rect || !rect.width) return null;
        const current = viewRef.current;
        return {
            x: current.x + ((clientX - rect.left) / rect.width) * current.w,
            y: current.y + ((clientY - rect.top) / rect.height) * viewHeight(current),
        };
    };

    /* «+» приближает к выбранному городу, если он на экране: центр страны —
       пустая степь, и приближение туда показывало бы белый лист. */
    const zoomBy = (factor, center = null) => {
        const current = viewRef.current;
        let focus = center;
        if (!focus && factor > 1 && selectedId) {
            const target = points.find((item) => item.city.id === selectedId)?.point;
            const inside = target && target.x >= current.x && target.x <= current.x + current.w
                && target.y >= current.y && target.y <= current.y + viewHeight(current);
            if (inside) focus = target;
        }
        const cx = focus ? focus.x : current.x + current.w / 2;
        const cy = focus ? focus.y : current.y + viewHeight(current) / 2;
        animateTo(zoomView(current, factor, cx, cy));
    };

    const resetView = () => {
        setFocusZone('');
        animateTo(FULL_VIEW);
    };

    const toggleZone = (zone) => {
        if (focusZone === zone) {
            resetView();
            return;
        }
        setFocusZone(zone);
        animateTo(fitView(points.filter((item) => item.zone === zone).map((item) => item.point)));
    };

    /* Выбрали город в списке, а он за краем приближённой схемы — подвозим. */
    useEffect(() => {
        if (!selectedId) return;
        const target = points.find((item) => item.city.id === selectedId)?.point;
        const current = viewRef.current;
        if (!target || current.w >= MAP_WIDTH - 0.5) return;
        const inside = target.x > current.x && target.x < current.x + current.w
            && target.y > current.y && target.y < current.y + viewHeight(current);
        if (!inside) {
            animateTo({ w: current.w, x: target.x - current.w / 2, y: target.y - viewHeight(current) / 2 });
        }
    }, [selectedId, points, animateTo]);

    // Щипок трекпада приходит колёсиком с ctrlKey; обычное колёсико — странице.
    useEffect(() => {
        const svg = svgRef.current;
        if (!svg) return undefined;
        const onWheel = (event) => {
            if (!event.ctrlKey && !event.metaKey) return;
            event.preventDefault();
            cancelAnimationFrame(frameRef.current);
            const rect = svg.getBoundingClientRect();
            if (!rect.width) return;
            setView((current) => {
                const at = {
                    x: current.x + ((event.clientX - rect.left) / rect.width) * current.w,
                    y: current.y + ((event.clientY - rect.top) / rect.height) * viewHeight(current),
                };
                return zoomView(current, Math.exp(-event.deltaY * 0.01), at.x, at.y);
            });
        };
        svg.addEventListener('wheel', onWheel, { passive: false });
        return () => svg.removeEventListener('wheel', onWheel);
    }, []);

    /* Перетаскивание и щипок двумя пальцами — pointer events на всей схеме.
       Нажатие без сдвига доходит до точки города обычным click; сдвиг больше
       DRAG_SLOP этот click гасит, иначе конец перетаскивания открывал бы
       город, над которым отпустили палец. */
    const onPointerDown = (event) => {
        if (event.button !== undefined && event.button !== 0) return;
        const gesture = gestureRef.current;
        gesture.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
        gesture.moved = false;
        gesture.start = { view: viewRef.current, pointers: new Map(gesture.pointers) };
        cancelAnimationFrame(frameRef.current);
    };

    const onPointerMove = (event) => {
        const gesture = gestureRef.current;
        if (!gesture.pointers.has(event.pointerId) || !gesture.start) return;
        gesture.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
        const rect = svgRef.current?.getBoundingClientRect();
        if (!rect?.width) return;
        const start = gesture.start;
        const perPixel = start.view.w / rect.width;

        if (gesture.pointers.size >= 2) {
            const [a, b] = [...gesture.pointers.values()];
            const [a0, b0] = [...start.pointers.values()];
            if (!a0 || !b0) return;
            const distance = Math.hypot(a.x - b.x, a.y - b.y);
            const distance0 = Math.hypot(a0.x - b0.x, a0.y - b0.y) || 1;
            const mid = { x: (a0.x + b0.x) / 2, y: (a0.y + b0.y) / 2 };
            const at = {
                x: start.view.x + (mid.x - rect.left) * perPixel,
                y: start.view.y + (mid.y - rect.top) * perPixel,
            };
            gesture.moved = true;
            setView(zoomView(start.view, distance / distance0, at.x, at.y));
            return;
        }

        const origin = start.pointers.get(event.pointerId);
        if (!origin) return;
        const dx = event.clientX - origin.x;
        const dy = event.clientY - origin.y;
        if (!gesture.moved && Math.hypot(dx, dy) < DRAG_SLOP) return;
        if (start.view.w >= MAP_WIDTH - 0.5) return;       // двигать нечего
        if (!gesture.moved) {
            gesture.moved = true;
            setDragging(true);
            svgRef.current?.setPointerCapture?.(event.pointerId);
        }
        setView(clampView({
            w: start.view.w,
            x: start.view.x - dx * perPixel,
            y: start.view.y - dy * perPixel,
        }));
    };

    const onPointerEnd = (event) => {
        const gesture = gestureRef.current;
        gesture.pointers.delete(event.pointerId);
        gesture.start = gesture.pointers.size
            ? { view: viewRef.current, pointers: new Map(gesture.pointers) }
            : null;
        if (!gesture.pointers.size) setDragging(false);
    };

    const onDoubleClick = (event) => {
        const at = toSvgPoint(event.clientX, event.clientY);
        if (at) zoomBy(2, at);
    };

    const pick = (city) => {
        if (gestureRef.current.moved) return;
        onSelect?.(city);
    };

    /* Всплывающая карточка — только у НАВЕДЁННОГО города. У выбранного её
       нет: всё, что в ней написано, уже стоит в карточке города рядом со
       схемой, а сама плашка закрывала соседние точки (Павлодар над
       Экибастузом). Выбранный отмечен ореолом и жирной подписью. */
    const callout = points.find((item) => item.city.id === hoverId) || null;
    let calloutStyle = null;
    if (callout) {
        const left = ((callout.point.x - view.x) / view.w) * width;
        const top = ((callout.point.y - view.y) / viewHeight(view)) * (width * ASPECT);
        if (left >= 0 && left <= width && top >= 0 && top <= width * ASPECT) {
            calloutStyle = { left, top };
        }
    }

    // Выбранный и наведённый — последними: в SVG нет z-index.
    const ordered = useMemo(() => [...points].sort((a, b) => {
        const rank = (item) => (item.city.id === selectedId ? 2 : item.city.id === hoverId ? 1 : 0);
        return rank(a) - rank(b);
    }), [points, selectedId, hoverId]);

    const labelSize = (compact ? 11 : 12.5) * unit;
    /* На телефоне схема шириной ~350 px, и кнопки в 32 px закрывали бы
       десятую часть её ширины — там они компактнее и прижаты к углу. */
    const buttonSize = compact ? 'h-7 w-7' : 'h-8 w-8';
    const inset = compact ? 'right-2 top-2' : 'right-3 top-3';

    const legend = (zones.length > 0 || points.some((item) => item.city.has_office)) && (
        <div className={`flex flex-wrap items-center gap-1 ${compact ? 'px-1 pt-2.5' : `${glass} absolute bottom-3 left-3 max-w-[calc(100%-24px)] rounded-xl p-1`}`}>
            {zones.map((zone) => {
                const active = focusZone === zone;
                return (
                    <button
                        key={zone}
                        type="button"
                        onClick={() => toggleZone(zone)}
                        aria-pressed={active}
                        className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] transition active:scale-[0.97] ${
                            active
                                ? 'bg-slate-900 font-medium text-white'
                                : focusZone
                                    ? 'text-slate-400 hover:bg-slate-900/[0.05] hover:text-slate-700'
                                    : 'text-slate-700 hover:bg-slate-900/[0.05]'
                        }`}
                    >
                        <span className="h-2 w-2 shrink-0 rounded-full ring-2 ring-white" style={{ background: palette[zone] }} />
                        Зона {cityGenitive(zone)}
                    </button>
                );
            })}
            <span className="inline-flex items-center gap-1.5 px-2 py-1 text-[11.5px] text-slate-400">
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                    <circle cx="6" cy="6" r="5" fill="#94a3b8" stroke="#fff" strokeWidth="1.5" />
                    <circle cx="6" cy="6" r="1.8" fill="#fff" />
                </svg>
                город со своим офисом
            </span>
        </div>
    );

    return (
        <div>
            <div
                ref={wrapRef}
                className={`relative overflow-hidden ${compact ? 'rounded-xl' : 'rounded-2xl'} select-none`}
            >
                <svg
                    ref={svgRef}
                    viewBox={`${view.x} ${view.y} ${view.w} ${viewHeight(view)}`}
                    className={`block h-auto w-full ${zoomed ? (dragging ? 'cursor-grabbing' : 'cursor-grab') : ''}`}
                    style={{ touchAction: zoomed ? 'none' : 'pan-y', aspectRatio: `${MAP_WIDTH} / ${MAP_HEIGHT}` }}
                    role="group"
                    aria-label="Города на карте"
                    onPointerDown={onPointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={onPointerEnd}
                    onPointerCancel={onPointerEnd}
                    onDoubleClick={onDoubleClick}
                >
                    <defs>
                        <linearGradient id={`${uid}-water`} x1="0" y1="0" x2="0.35" y2="1">
                            <stop offset="0" stopColor="#e3eefa" />
                            <stop offset="1" stopColor="#d3e4f5" />
                        </linearGradient>
                        <filter id={`${uid}-land`} x="-5%" y="-8%" width="110%" height="116%">
                            <feDropShadow dx="0" dy="2" stdDeviation="4" floodColor="#0f172a" floodOpacity="0.13" />
                        </filter>
                        <filter id={`${uid}-pin`} x="-60%" y="-60%" width="220%" height="220%">
                            <feDropShadow dx="0" dy={0.8 * unit} stdDeviation={1.2 * unit}
                                          floodColor="#0f172a" floodOpacity="0.28" />
                        </filter>
                    </defs>

                    <rect x="-50" y="-50" width={MAP_WIDTH + 100} height={MAP_HEIGHT + 100} fill={`url(#${uid}-water)`} />
                    <path d={NEIGHBORS_PATH} fill="#eef1f5" stroke="#ffffff" strokeWidth={1.1 * unit} strokeLinejoin="round" />
                    <path d={KAZAKHSTAN_PATH} fill="#ffffff" stroke="#cbd5e1" strokeWidth={0.9 * unit}
                          strokeLinejoin="round" filter={`url(#${uid}-land)`} />
                    <path d={RIVERS_PATH} fill="none" stroke="#bcd7f1" strokeWidth={1.3 * unit}
                          strokeLinecap="round" strokeLinejoin="round" />
                    <path d={LAKES_PATH} fill="#d3e4f5" stroke="#bcd7f1" strokeWidth={0.7 * unit} />

                    {ordered.map(({ city, point, zone }) => {
                        const color = palette[zone] || NO_ZONE_COLOR;
                        const big = !!city.has_office;
                        const selected = city.id === selectedId;
                        const hovered = city.id === hoverId;
                        const dimmed = !!focusZone && zone !== focusZone;
                        const r = ((big ? 7.5 : 5) + (selected ? 1.5 : hovered ? 1 : 0)) * unit;
                        const inCallout = !!calloutStyle && callout.city.id === city.id;
                        const showLabel = !dimmed && !inCallout
                            && (!compact || big || selected || deepZoom);
                        const label = labelPosition(point.side, point.x, point.y, r + 6 * unit, labelSize);
                        return (
                            <g
                                key={city.id}
                                role="button"
                                tabIndex={0}
                                aria-label={city.name}
                                aria-pressed={selected}
                                onClick={() => pick(city)}
                                onPointerEnter={(event) => { if (event.pointerType === 'mouse') setHoverId(city.id); }}
                                onPointerLeave={() => setHoverId((current) => (current === city.id ? null : current))}
                                onFocus={() => setHoverId(city.id)}
                                onBlur={() => setHoverId((current) => (current === city.id ? null : current))}
                                onKeyDown={(event) => {
                                    if (event.key === 'Enter' || event.key === ' ') {
                                        event.preventDefault();
                                        onSelect?.(city);
                                    }
                                }}
                                opacity={dimmed ? 0.28 : 1}
                                className="cursor-pointer outline-none"
                                style={{ transition: 'opacity 200ms ease' }}
                            >
                                {/* Мишень шире точки: палец в 5 px не попадает. */}
                                <circle cx={point.x} cy={point.y} r={(compact ? 20 : 14) * unit} fill="transparent" />
                                {selected && (
                                    <>
                                        <circle cx={point.x} cy={point.y} r={r + 8 * unit} fill={color} opacity="0.16" />
                                        <circle cx={point.x} cy={point.y} r={r + 8 * unit} fill="none"
                                                stroke={color} strokeOpacity="0.45" strokeWidth={1.2 * unit} />
                                    </>
                                )}
                                <circle
                                    cx={point.x}
                                    cy={point.y}
                                    r={r}
                                    fill={color}
                                    stroke="#fff"
                                    strokeWidth={(big ? 2.5 : 2) * unit}
                                    filter={`url(#${uid}-pin)`}
                                />
                                {big && <circle cx={point.x} cy={point.y} r={r * 0.36} fill="#fff" />}
                                {showLabel && (
                                    <text
                                        {...label}
                                        fontSize={labelSize}
                                        fontWeight={big || selected || hovered ? 600 : 450}
                                        fill={selected ? '#0f172a' : big ? '#334155' : '#64748b'}
                                        stroke="#fff"
                                        strokeWidth={3.2 * unit}
                                        strokeLinejoin="round"
                                        paintOrder="stroke"
                                        style={{ fontFamily: 'inherit', letterSpacing: '-0.01em', pointerEvents: 'none' }}
                                    >
                                        {city.name}
                                    </text>
                                )}
                            </g>
                        );
                    })}
                </svg>

                {/* Карточка над точкой — HTML, а не SVG: у неё настоящие
                    скругления, размытие и шрифт, и размер не зависит от
                    приближения. Щелчки проходят сквозь неё к схеме. */}
                {calloutStyle && (
                    <div
                        className="pointer-events-none absolute z-10"
                        style={{ left: calloutStyle.left, top: calloutStyle.top }}
                    >
                        <div
                            className={`${glass} whitespace-nowrap rounded-xl px-3 py-2 text-left`}
                            style={{ transform: `translate(-50%, calc(-100% - ${(callout.city.has_office ? 7.5 : 5) + 12}px))` }}
                        >
                            <div className="text-[13px] font-semibold leading-tight text-slate-900">{callout.city.name}</div>
                            <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] leading-tight text-slate-500">
                                {callout.zone && (
                                    <>
                                        <span className="h-1.5 w-1.5 rounded-full" style={{ background: palette[callout.zone] }} />
                                        <span>Зона {cityGenitive(callout.zone)}</span>
                                        <span className="text-slate-300">·</span>
                                    </>
                                )}
                                <span className="tabular-nums">
                                    {callout.city.tariff_count ? pluralTariffs(callout.city.tariff_count) : 'тарифов нет'}
                                </span>
                            </div>
                        </div>
                    </div>
                )}

                {/* Кнопки приближения — стеклянной плашкой в углу, как в «Картах». */}
                <div className={`${glass} absolute ${inset} flex flex-col overflow-hidden rounded-xl`}>
                    <button type="button" className={`${controlButton} ${buttonSize}`} onClick={() => zoomBy(1.8)}
                            disabled={view.w <= MAP_WIDTH / 5 + 0.5}
                            aria-label="Приблизить" title="Приблизить — или двойной щелчок, щипок">
                        <Plus size={15} strokeWidth={2.2} />
                    </button>
                    <div className="mx-1.5 h-px bg-slate-900/[0.08]" />
                    <button type="button" className={`${controlButton} ${buttonSize}`} onClick={() => zoomBy(1 / 1.8)}
                            disabled={!zoomed} aria-label="Отдалить" title="Отдалить">
                        <Minus size={15} strokeWidth={2.2} />
                    </button>
                </div>
                {(zoomed || focusZone) && (
                    <button
                        type="button"
                        onClick={resetView}
                        className={`${glass} ${controlButton} ${buttonSize} absolute rounded-xl ${compact ? 'right-2 top-[70px]' : 'right-3 top-[86px]'}`}
                        aria-label="Вся страна"
                        title="Вся страна"
                    >
                        <Maximize2 size={14} strokeWidth={2.2} />
                    </button>
                )}

                {!compact && legend}
            </div>
            {compact && legend}
        </div>
    );
}
