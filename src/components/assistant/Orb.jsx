import React, { useEffect, useState } from 'react';
import IcoreAssistantMark from './IcoreAssistantMark.jsx';
import { CAT_APPEARANCE, PUMPKIN_APPEARANCE } from './assistantAppearance.js';
import { isHalloweenTime } from '../seasonal/halloween/halloweenWindow.js';
import './assistant-orb.css';

// One transparent mark for the floating button, panel avatar and empty chat.
// Keep the existing dimensions and shell so dragging/docking geometry is stable.

/* Облики-талисманы (тыква, котик) — каждый своим чанком, без Suspense: пока он
   едет, на его месте прежний знак, а не доехал — знак насовсем, без экрана
   ошибки. Кому талисман нужен сразу (тыква в праздник — всем, потом — тем, кто
   выбрал облик), тем он грузится со стартом портала и встаёт в шарик без смены
   на глазах. Map, а не объект: облик из хранилища не должен найти «constructor». */
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

const mascotsChosenHere = () => {
    const chosen = new Set();
    try {
        for (let i = 0; i < window.localStorage.length; i += 1) {
            const key = window.localStorage.key(i) || '';
            // Праздничный выбор после праздника не в счёт — облик решает основной ключ.
            if (!key.startsWith('otp:assistant:appearance:') || key.endsWith(':halloween-2026')) continue;
            const value = window.localStorage.getItem(key);
            if (MASCOT_CHUNKS.has(value)) chosen.add(value);
        }
    } catch {
        // Хранилище закрыто — талисман подгрузится, когда его покажут.
    }
    return chosen;
};

if (typeof window !== 'undefined') {
    const early = mascotsChosenHere();
    if (isHalloweenTime()) early.add(PUMPKIN_APPEARANCE);
    early.forEach(loadMascot);
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
