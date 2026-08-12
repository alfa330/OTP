import React, { useEffect, useMemo, useRef, useState } from 'react';
import { MapPin } from 'lucide-react';

/* Мини-карта офиса: мозаика тайлов 2ГИС и точка в центре.
 *
 * Без библиотеки карт и без ключа API. Растровые тайлы 2ГИС отдаются с
 * Access-Control-Allow-Origin: * и без проверки Referer, кэш сутки — для
 * статичной картинки «где офис» этого достаточно, а MapGL потянул бы за собой
 * ключ, тарификацию запросов и 200 КБ скриптов ради двух десятков точек.
 *
 * Карта намеренно неинтерактивна: клик открывает ту же точку в 2ГИС, где есть
 * маршруты и панорамы. Повторять их внутри портала незачем.
 *
 * Если понадобится официальный тариф (Raster Tiles API с ключом) — меняется
 * только tileUrl: остальная математика от источника тайлов не зависит.
 */

const TILE = 256;

const tileUrl = (x, y, z) => (
    // Хосты чередуются по номеру тайла: браузер держит ограниченное число
    // соединений на домен, и 4–6 картинок с одного хоста грузятся по очереди.
    `https://tile${(x + y) % 4}.maps.2gis.com/tiles?x=${x}&y=${y}&z=${z}&v=1`
);

/** Координаты → мировые пиксели проекции Меркатора на данном зуме. */
const project = (lat, lon, zoom) => {
    const scale = TILE * (2 ** zoom);
    const latRad = (lat * Math.PI) / 180;
    return {
        x: ((lon + 180) / 360) * scale,
        y: ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * scale,
    };
};

export default function OfficeMap({ lat, lon, url, zoom = 16, height = 148, className = '' }) {
    const boxRef = useRef(null);
    const [width, setWidth] = useState(0);

    useEffect(() => {
        const node = boxRef.current;
        if (!node) return undefined;
        // Ширина карточки зависит от сетки (одна или две колонки), поэтому
        // измеряем, а не считаем: иначе на узком экране мозаика не сойдётся.
        const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
        observer.observe(node);
        setWidth(node.getBoundingClientRect().width);
        return () => observer.disconnect();
    }, []);

    const tiles = useMemo(() => {
        if (!width || lat == null || lon == null) return [];
        const center = project(lat, lon, zoom);
        const left = center.x - width / 2;
        const top = center.y - height / 2;
        const limit = 2 ** zoom;

        const result = [];
        for (let x = Math.floor(left / TILE); x <= Math.floor((left + width - 1) / TILE); x += 1) {
            for (let y = Math.floor(top / TILE); y <= Math.floor((top + height - 1) / TILE); y += 1) {
                // Мир по долготе замкнут, по широте — нет: тайлы за полюсом не
                // существуют, и запрос за ними вернул бы 404 вместо картинки.
                if (y < 0 || y >= limit) continue;
                result.push({
                    key: `${x}:${y}`,
                    src: tileUrl(((x % limit) + limit) % limit, y, zoom),
                    left: Math.round(x * TILE - left),
                    top: Math.round(y * TILE - top),
                });
            }
        }
        return result;
    }, [width, height, lat, lon, zoom]);

    if (lat == null || lon == null) return null;

    const body = (
        <>
            {tiles.map((tile) => (
                <img
                    key={tile.key}
                    src={tile.src}
                    alt=""
                    aria-hidden="true"
                    draggable={false}
                    loading="lazy"
                    width={TILE}
                    height={TILE}
                    className="pointer-events-none absolute max-w-none select-none"
                    style={{ left: tile.left, top: tile.top }}
                />
            ))}

            {/* Точка ровно в центре: карта построена так, что центр мозаики и
                есть офис. Хвост булавки указывает в точку, поэтому смещаем её
                вверх на всю высоту значка. */}
            <span
                className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-full drop-shadow"
                aria-hidden="true"
            >
                <MapPin size={26} className="fill-rose-500 text-white" strokeWidth={1.75} />
            </span>

            <span className="pointer-events-none absolute bottom-1 right-1.5 rounded bg-white/80 px-1 text-[9px] font-medium leading-4 text-slate-500">
                2ГИС
            </span>
        </>
    );

    const shell = `relative block overflow-hidden rounded-xl bg-slate-100 ${className}`;

    return url ? (
        <a
            ref={boxRef}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            style={{ height }}
            className={`${shell} transition hover:opacity-95 active:scale-[0.99]`}
            aria-label="Открыть офис в 2ГИС"
        >
            {body}
        </a>
    ) : (
        <div ref={boxRef} style={{ height }} className={shell}>{body}</div>
    );
}
