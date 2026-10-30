import { useEffect, useState } from 'react';
import { isHalloweenTime, watchHalloweenEnd } from './halloweenWindow';

/* Идёт ли Хэллоуин — с гашением в открытой вкладке (watchHalloweenEnd: таймер
   до полуночи, проверка раз в минуту и при возвращении на вкладку). */
export default function useHalloweenActive() {
    const [active, setActive] = useState(() => isHalloweenTime());
    useEffect(() => {
        if (!active) return undefined;
        return watchHalloweenEnd(() => setActive(false));
    }, [active]);
    return active;
}
