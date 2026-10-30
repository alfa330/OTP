import { useEffect, useState } from 'react';
import { isHalloweenTime } from './halloweenWindow';

/* Идёт ли Хэллоуин — с гашением в открытой вкладке. Проверка раз в минуту, а
   не один таймер до полуночи: таймеры спят вместе с ноутбуком и просыпаются
   с опозданием, а минутный опрос после сна сработает сразу. */
export default function useHalloweenActive() {
    const [active, setActive] = useState(() => isHalloweenTime());
    useEffect(() => {
        if (!active) return undefined;
        const check = () => {
            if (!isHalloweenTime()) setActive(false);
        };
        const timer = window.setInterval(check, 60000);
        document.addEventListener('visibilitychange', check);
        window.addEventListener('focus', check);
        return () => {
            window.clearInterval(timer);
            document.removeEventListener('visibilitychange', check);
            window.removeEventListener('focus', check);
        };
    }, [active]);
    return active;
}
