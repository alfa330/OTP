import React, { useEffect, useMemo, useRef, useState } from 'react';
import Backgrounds from './Backgrounds';

/*
 * Фон сцены в «Альбоме» — фон, выбранный в разметке у фото разворота.
 * Смена — наплывом: новый слой проявляется, прежний гаснет и снимается по
 * окончании своей анимации (album.css: .fy-album-backdrop). Так фон не
 * обрывается за кадр ни при листании, ни когда книга закрывается или уходит
 * (тогда приходит 'none' и прежний слой просто гаснет).
 *
 * Прозрачность у слоёв своя, не от карточки ленты: лента гасит свой фон по
 * selectedMix, и один не мешает другому.
 */
const prefersReducedMotion = () => (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
);

const AlbumBackdrop = ({ bg }) => {
    const reduced = useMemo(prefersReducedMotion, []);
    const [layers, setLayers] = useState([]);   // [{ id, bg, leaving }]
    const seqRef = useRef(0);
    const target = bg && bg !== 'none' ? bg : 'none';

    useEffect(() => {
        setLayers((prev) => {
            const current = prev.find((layer) => !layer.leaving);
            if ((current ? current.bg : 'none') === target) return prev;
            // Без анимаций уходящие слои снимаются сразу: конца анимации не будет.
            const kept = reduced
                ? []
                : prev.map((layer) => (layer.leaving ? layer : { ...layer, leaving: true }));
            if (target === 'none') return kept;
            seqRef.current += 1;
            return [...kept, { id: seqRef.current, bg: target, leaving: false }];
        });
    }, [target, reduced]);

    const drop = (id) => setLayers((prev) => prev.filter((layer) => layer.id !== id));

    return layers.map((layer) => (
        <div
            key={layer.id}
            className={`fy-album-backdrop${layer.leaving ? ' is-leaving' : ''}`}
            aria-hidden="true"
            onAnimationEnd={(event) => {
                // Частицы фона анимируются бесконечно — слушаем только сам слой.
                if (layer.leaving && event.target === event.currentTarget) drop(layer.id);
            }}
        >
            <Backgrounds bg={layer.bg} />
        </div>
    ));
};

export default AlbumBackdrop;
