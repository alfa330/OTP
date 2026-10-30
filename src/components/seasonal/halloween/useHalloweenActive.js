import { useEffect, useState } from 'react';
import { HALLOWEEN_ENDS_AT, isHalloweenTime } from './halloweenWindow';

/* Идёт ли Хэллоуин — с гашением в открытой вкладке: один таймер ровно до
   полуночи плюс проверка при возвращении на вкладку и в окно (таймеры спят
   вместе с ноутбуком и просыпаются с опозданием). Таймер — глобальный и с
   unref там, где он есть (node в тестах): долгий таймер не держит процесс. */
export default function useHalloweenActive() {
    const [active, setActive] = useState(() => isHalloweenTime());
    useEffect(() => {
        if (!active) return undefined;
        const check = () => {
            if (!isHalloweenTime()) setActive(false);
        };
        const left = HALLOWEEN_ENDS_AT - Date.now();
        const timer = setTimeout(check, Math.max(0, Math.min(left + 50, 2 ** 31 - 1)));
        timer?.unref?.();
        window.addEventListener?.('focus', check);
        window.addEventListener?.('pageshow', check);
        document.addEventListener?.('visibilitychange', check);
        return () => {
            clearTimeout(timer);
            window.removeEventListener?.('focus', check);
            window.removeEventListener?.('pageshow', check);
            document.removeEventListener?.('visibilitychange', check);
        };
    }, [active]);
    return active;
}
