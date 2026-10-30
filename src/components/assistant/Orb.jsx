import React, { useEffect, useState } from 'react';
import IcoreAssistantMark from './IcoreAssistantMark.jsx';
import { PUMPKIN_APPEARANCE } from './assistantAppearance.js';
import { isHalloweenTime } from '../seasonal/halloween/halloweenWindow.js';
import './assistant-orb.css';

// One transparent mark for the floating button, panel avatar and empty chat.
// Keep the existing dimensions and shell so dragging/docking geometry is stable.

/* Облик «Тыква» (pumpkin/PumpkinMascot.jsx) — своим чанком, без Suspense: пока он
   едет, на месте тыквы прежний знак, а не доехал — знак насовсем, без экрана
   ошибки. Кому тыква нужна сразу (в праздник — всем, потом — тем, кто её выбрал),
   тем она грузится со стартом портала и встаёт в шарик без смены на глазах. */
let LoadedPumpkin = null;
let pumpkinPromise = null;
const loadPumpkin = () => {
    if (!pumpkinPromise) {
        pumpkinPromise = import('./pumpkin/PumpkinMascot.jsx')
            .then((module) => { LoadedPumpkin = module?.default || null; })
            .catch(() => { pumpkinPromise = null; });
    }
    return pumpkinPromise;
};

const pumpkinChosenHere = () => {
    try {
        for (let i = 0; i < window.localStorage.length; i += 1) {
            const key = window.localStorage.key(i) || '';
            // Праздничный выбор после праздника не в счёт — облик решает основной ключ.
            if (!key.startsWith('otp:assistant:appearance:') || key.endsWith(':halloween-2026')) continue;
            if (window.localStorage.getItem(key) === PUMPKIN_APPEARANCE) return true;
        }
    } catch {
        // Хранилище закрыто — тыква подгрузится, когда её покажут.
    }
    return false;
};

if (typeof window !== 'undefined' && (isHalloweenTime() || pumpkinChosenHere())) loadPumpkin();

const VARIANT_CLASS = {
    orb: '',
    mini: 'aorb--mini',
    hero: 'aorb--hero',
};

const VARIANT_SIZE = { orb: 56, mini: 26, hero: 64 };

const Orb = ({ variant = 'orb', effect, animated = true, className = '' }) => {
    const wantsPumpkin = effect === PUMPKIN_APPEARANCE;
    const [, setLoaded] = useState(Boolean(LoadedPumpkin));
    useEffect(() => {
        if (!wantsPumpkin || LoadedPumpkin) return undefined;
        let alive = true;
        loadPumpkin().then(() => {
            if (alive && LoadedPumpkin) setLoaded(true);
        });
        return () => { alive = false; };
    }, [wantsPumpkin]);
    const Pumpkin = wantsPumpkin ? LoadedPumpkin : null;
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
