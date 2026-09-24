import React, { useMemo } from 'react';
import { KAZAKHSTAN_PATH, MAP_HEIGHT, MAP_WIDTH, cityPoint } from './kazakhstanMap';
import {
    NO_ZONE_COLOR, cityGenitive, cityZone, zoneHubs, zonePalette,
} from './cityRules';

/* Схема городов по макету постановки #322: контур страны, точка на город,
 * цвет — зона обслуживающего офиса, крупная точка — город со своим офисом.
 *
 * Схема — навигация, а не справочник: нажатие на точку открывает карточку
 * города так же, как строка списка. Поэтому у каждой точки есть и мишень
 * побольше самой точки (на телефоне точка 5 px при ширине схемы 360 px), и
 * доступ с клавиатуры.
 *
 * compact — телефон: схема ужата втрое, и подписи всех городов налезли бы
 * друг на друга. Остаются подписи городов с офисом и выбранного — те, ради
 * которых на схему и смотрят.
 */

const LABEL_GAP = 7;

const labelProps = (point, r, fontSize) => {
    switch (point.side) {
        case 'left':
            return { x: point.x - r - LABEL_GAP, y: point.y + fontSize * 0.35, textAnchor: 'end' };
        case 'top':
            return { x: point.x, y: point.y - r - LABEL_GAP, textAnchor: 'middle' };
        case 'bottom':
            return { x: point.x, y: point.y + r + LABEL_GAP + fontSize * 0.75, textAnchor: 'middle' };
        default:
            return { x: point.x + r + LABEL_GAP, y: point.y + fontSize * 0.35, textAnchor: 'start' };
    }
};

export default function CityMap({ cities, selectedId, onSelect, compact = false }) {
    const palette = useMemo(() => zonePalette(cities), [cities]);
    const hubs = useMemo(() => zoneHubs(cities), [cities]);

    const points = useMemo(() => {
        const list = (cities || [])
            .map((city) => ({ city, point: cityPoint(city.name), zone: cityZone(city, hubs) }))
            .filter((item) => item.point);
        // Выбранный — последним: в SVG нет z-index, и ореол соседа иначе лёг
        // бы поверх выбранной точки.
        return list.sort((a, b) => (a.city.id === selectedId) - (b.city.id === selectedId));
    }, [cities, selectedId, hubs]);

    const legend = useMemo(() => {
        const zones = Object.keys(palette).sort(new Intl.Collator('ru').compare);
        const withoutZone = (cities || []).some((city) => !cityZone(city, hubs) && cityPoint(city.name));
        return { zones, withoutZone: withoutZone && zones.length > 0 };
    }, [palette, cities, hubs]);

    const fontSize = compact ? 26 : 15;

    return (
        <div>
            <svg
                viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
                className="block h-auto w-full select-none"
                role="group"
                aria-label="Города на карте"
            >
                <path
                    d={KAZAKHSTAN_PATH}
                    fill="#f8fafc"
                    stroke="#cbd5e1"
                    strokeWidth="1.5"
                    strokeLinejoin="round"
                />
                {points.map(({ city, point, zone }) => {
                    const color = palette[zone] || NO_ZONE_COLOR;
                    const big = !!city.has_office;
                    const selected = city.id === selectedId;
                    const r = (big ? 8 : 5.5) * (compact ? 1.6 : 1);
                    const showLabel = !compact || big || selected;
                    const label = labelProps(point, r, fontSize);
                    const pick = () => onSelect?.(city);
                    return (
                        <g
                            key={city.id}
                            role="button"
                            tabIndex={0}
                            aria-label={city.name}
                            aria-pressed={selected}
                            onClick={pick}
                            onKeyDown={(event) => {
                                if (event.key === 'Enter' || event.key === ' ') {
                                    event.preventDefault();
                                    pick();
                                }
                            }}
                            className="cursor-pointer outline-none [&:focus-visible>circle:last-of-type]:stroke-blue-500"
                        >
                            {/* Мишень шире точки — палец на телефоне в 5 px
                                не попадёт. Прозрачная, но участвует в клике. */}
                            <circle cx={point.x} cy={point.y} r={compact ? 34 : 16} fill="transparent" />
                            {selected && (
                                <circle cx={point.x} cy={point.y} r={r + 8} fill={color} opacity="0.2" />
                            )}
                            <circle
                                cx={point.x}
                                cy={point.y}
                                r={r}
                                fill={color}
                                stroke="#fff"
                                strokeWidth={compact ? 3 : 2}
                            />
                            {showLabel && (
                                <text
                                    {...label}
                                    fontSize={fontSize}
                                    fontWeight={big || selected ? 600 : 400}
                                    fill={selected ? '#0f172a' : big ? '#334155' : '#64748b'}
                                    stroke="#fff"
                                    strokeWidth={compact ? 6 : 4}
                                    strokeLinejoin="round"
                                    paintOrder="stroke"
                                    style={{ fontFamily: 'inherit' }}
                                >
                                    {city.name}
                                </text>
                            )}
                        </g>
                    );
                })}
            </svg>

            {/* Легенда — только когда зоны заданы: «крупная точка — город с
                офисом» без цветов зон всё равно нужна, а список зон из одной
                серой «без зоны» — нет. */}
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 px-1 text-[12px] text-slate-600">
                {legend.zones.map((zone) => (
                    <span key={zone} className="inline-flex items-center gap-1.5">
                        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: palette[zone] }} />
                        Зона {cityGenitive(zone)}
                    </span>
                ))}
                {legend.withoutZone && (
                    <span className="inline-flex items-center gap-1.5">
                        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: NO_ZONE_COLOR }} />
                        Зона не задана
                    </span>
                )}
                <span className="text-slate-400">Крупная точка — город с офисом</span>
            </div>
        </div>
    );
}
