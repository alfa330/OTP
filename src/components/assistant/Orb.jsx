import React, { useEffect, useState } from 'react';
import IcoreAssistantMark from './IcoreAssistantMark.jsx';
import useHalloweenActive from '../seasonal/halloween/useHalloweenActive.js';
import { isHalloweenTime } from '../seasonal/halloween/halloweenWindow.js';
import './assistant-orb.css';

// One transparent mark for the floating button, panel avatar and empty chat.
// Keep the existing dimensions and shell so dragging/docking geometry is stable.

/* Хэллоуин-2026 (до конца 1 ноября): на месте знака — тыква с эмоциями, своим
   чанком; после срока не грузится. Грузится сразу с порталом, без Suspense:
   пока портал проверяет сессию, тыква уже приехала и встаёт на место знака без
   смены на глазах. Не доехала — остаётся прежний знак, без экрана ошибки. */
let LoadedPumpkin = null;
const pumpkinReady = isHalloweenTime()
    ? import('../seasonal/halloween/PumpkinMascot.jsx')
        .then((module) => { LoadedPumpkin = module?.default || null; })
        .catch(() => {})
    : null;

const VARIANT_CLASS = {
    orb: '',
    mini: 'aorb--mini',
    hero: 'aorb--hero',
};

const VARIANT_SIZE = { orb: 56, mini: 26, hero: 64 };

const Orb = ({ variant = 'orb', effect, animated = true, className = '' }) => {
    const halloween = useHalloweenActive();
    const [, setLoaded] = useState(Boolean(LoadedPumpkin));
    useEffect(() => {
        if (!halloween || LoadedPumpkin || !pumpkinReady) return undefined;
        let alive = true;
        pumpkinReady.then(() => {
            if (alive && LoadedPumpkin) setLoaded(true);
        });
        return () => { alive = false; };
    }, [halloween]);
    const Pumpkin = halloween ? LoadedPumpkin : null;
    return (
        <span
            className={`aorb ${VARIANT_CLASS[variant] || ''} ${className}`}
            aria-hidden="true"
        >
            <span className="aorb-shell">
                {Pumpkin
                    ? <Pumpkin variant={variant} animated={animated} />
                    : <IcoreAssistantMark effect={effect} size={VARIANT_SIZE[variant] || 56} animated={animated} />}
            </span>
        </span>
    );
};

export default Orb;
