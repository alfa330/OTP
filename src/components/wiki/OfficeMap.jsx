import React, { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { MapPin } from 'lucide-react';

/* Мини-карта офиса: мозаика тайлов 2ГИС и точка в центре.
 *
 * Без библиотеки карт и без ключа API: MapGL потянул бы ключ, тарификацию
 * запросов и 200 КБ скриптов ради двух десятков неподвижных точек.
 *
 * Тайлы идут сначала через наш прокси (/api/wiki/map/tile/...) и только потом
 * прямо с tile*.maps.2gis.com. Прокси первым потому, что у него кэш: 2ГИС
 * режет пачку запросов с одного адреса, и на странице с пятнадцатью офисами
 * четыре карты приходили пустыми. Прямая загрузка вторым потому, что 2ГИС
 * перестал отвечать самому серверу (подробности у tileSources).
 *
 * Карта намеренно неинтерактивна: клик открывает ту же точку в 2ГИС, где есть
 * маршруты и панорамы. Повторять их внутри портала незачем.
 */

const TILE = 256;

/* Откуда берётся картинка тайла, по порядку.
 *
 * Сначала наш прокси: у него кэш, и повторное открытие раздела не стоит ни
 * одного запроса к 2ГИС. Если он ответил пусто — идём к 2ГИС напрямую, и это
 * не «на всякий случай»: замер 21.08.2026 показал, что tile*.maps.2gis.com не
 * отвечает нашему серверу во Франкфурте вовсе (соединение висит до таймаута),
 * а браузеру сотрудника в Казахстане отдаёт тот же тайл за 470 мс. Хосты
 * перебираем, потому что пачку запросов с одного адреса 2ГИС режет.
 *
 * Не вышло ниоткуда — убираем картинку совсем: серый фон мозаики честнее
 * значка битой картинки. */
const tileSources = (base, z, x, y) => [
    `${base}/map/tile/${z}/${x}/${y}.png`,
    `https://tile${(x + y) % 4}.maps.2gis.com/tiles?x=${x}&y=${y}&z=${z}&v=1`,
    `https://tile${(x + y + 2) % 4}.maps.2gis.com/tiles?x=${x}&y=${y}&z=${z}&v=1`,
];

const Tile = ({ base, x, y, z, left, top }) => {
    const [attempt, setAttempt] = useState(0);
    const sources = tileSources(base, z, x, y);
    if (attempt >= sources.length) return null;
    return (
        <img
            src={sources[attempt]}
            alt=""
            aria-hidden="true"
            draggable={false}
            /* Не lazy: мозаика — это и есть содержимое карточки, а не картинка
               в тексте. С lazy браузер откладывал её до конца раскладки, и
               карта появлялась заметно позже самой карточки. */
            width={TILE}
            height={TILE}
            onError={() => setAttempt((n) => n + 1)}
            className="pointer-events-none absolute max-w-none select-none"
            style={{ left, top }}
        />
    );
};

/** Координаты → мировые пиксели проекции Меркатора на данном зуме. */
const project = (lat, lon, zoom) => {
    const scale = TILE * (2 ** zoom);
    const latRad = (lat * Math.PI) / 180;
    return {
        x: ((lon + 180) / 360) * scale,
        y: ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * scale,
    };
};

export default function OfficeMap({ base, lat, lon, url, zoom = 16, height = 148, className = '' }) {
    const boxRef = useRef(null);
    const [width, setWidth] = useState(0);

    // Замер до отрисовки, а не после: мозаика считается от ширины, и с обычным
    // useEffect первый кадр карточка показывала пустой серый прямоугольник —
    // запросы тайлов уходили только со второго рендера.
    useLayoutEffect(() => {
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
                    x: ((x % limit) + limit) % limit,
                    y,
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
                <Tile
                    key={tile.key}
                    base={base}
                    x={tile.x}
                    y={tile.y}
                    z={zoom}
                    left={tile.left}
                    top={tile.top}
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
