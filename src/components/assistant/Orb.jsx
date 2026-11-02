import React, { useEffect, useState } from 'react';
import IcoreAssistantMark from './IcoreAssistantMark.jsx';
import { CAT_APPEARANCE, PUMPKIN_APPEARANCE, mascotsToPreload } from './assistantAppearance.js';
import { isHalloweenTime } from '../seasonal/halloween/halloweenWindow.js';
import './assistant-orb.css';

// One transparent mark for the floating button, panel avatar and empty chat.
// Keep the existing dimensions and shell so dragging/docking geometry is stable.

/* Облики-талисманы (тыква, котик) — каждый своим чанком, без Suspense: пока он
   едет, на его месте прежний знак, а не доехал — знак насовсем, без экрана
   ошибки. Кому талисман нужен сразу (тыква в праздник — всем, потом — тем, кто
   выбрал облик), тем он грузится со стартом портала и встаёт в шарик без смены
   на глазах (mascotsToPreload). Map, а не объект: облик из хранилища не должен
   найти «constructor». */
const MASCOT_CHUNKS = new Map([
    [PUMPKIN_APPEARANCE, () => import('./pumpkin/PumpkinMascot.jsx')],
    [CAT_APPEARANCE, () => import('./cat/CatMascot.jsx')],
]);
const loadedMascots = new Map();
const mascotPromises = new Map();
const loadMascot = (id) => {
    if (!mascotPromises.has(id)) {
        mascotPromises.set(id, MASCOT_CHUNKS.get(id)()
            .then((module) => { loadedMascots.set(id, module?.default || null); })
            .catch(() => { mascotPromises.delete(id); }));
    }
    return mascotPromises.get(id);
};

if (typeof window !== 'undefined') {
    let storage = null;
    try {
        storage = window.localStorage;
    } catch {
        // Хранилище закрыто — mascotsToPreload обойдётся без него.
    }
    mascotsToPreload(storage, isHalloweenTime()).forEach(loadMascot);
}

const VARIANT_CLASS = {
    orb: '',
    mini: 'aorb--mini',
    hero: 'aorb--hero',
};

const VARIANT_SIZE = { orb: 56, mini: 26, hero: 64 };

const Orb = ({ variant = 'orb', effect, animated = true, className = '' }) => {
    const mascot = MASCOT_CHUNKS.has(effect) ? effect : null;
    const [, setLoaded] = useState(0);
    useEffect(() => {
        if (!mascot || loadedMascots.get(mascot)) return undefined;
        let alive = true;
        loadMascot(mascot).then(() => {
            if (alive && loadedMascots.get(mascot)) setLoaded((n) => n + 1);
        });
        return () => { alive = false; };
    }, [mascot]);
    const Mascot = mascot ? loadedMascots.get(mascot) : null;
    return (
        <span
            className={`aorb ${VARIANT_CLASS[variant] || ''} ${className}`}
            aria-hidden="true"
        >
            <span className="aorb-shell">
                {Mascot
                    ? <Mascot variant={variant} animated={animated} />
                    : <IcoreAssistantMark effect={effect} size={VARIANT_SIZE[variant] || 56} animated={animated} />}
            </span>
        </span>
    );
};

export default Orb;
