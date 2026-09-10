import { useEffect, useState } from 'react';
import { MOBILE_SHELL_QUERY } from '../../utils/mobileShell';

/* «Мы на телефоне» для примитивов, которым нужен только ОТВЕТ.
 *
 * Отдельно от useMobileShell (MobileTabBar): тот кроме ответа ещё и ставит
 * класс на <body> и держит сторону бара. Позвать его из общего примитива
 * нельзя — модалок на экране бывает несколько, и каждая начала бы снимать и
 * ставить класс оболочки, гася её на чужих размонтированиях.
 *
 * Запрос ОДИН на весь портал и живёт в src/utils/mobileShell.js: второй его
 * копии быть не должно — разъехавшись, они дают экран, который для разметки
 * телефонный, а для стилей настольный.
 */
export default function useIsMobileShell() {
    const [narrow, setNarrow] = useState(() => (
        typeof window !== 'undefined' && typeof window.matchMedia === 'function'
            ? window.matchMedia(MOBILE_SHELL_QUERY).matches
            : false
    ));

    useEffect(() => {
        if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
        const media = window.matchMedia(MOBILE_SHELL_QUERY);
        const handle = () => setNarrow(media.matches);
        handle();
        /* addListener — для Safari старше 14: там addEventListener у
           MediaQueryList ещё нет, а портал открывают и с таких устройств. */
        if (typeof media.addEventListener === 'function') media.addEventListener('change', handle);
        else if (typeof media.addListener === 'function') media.addListener(handle);
        return () => {
            if (typeof media.removeEventListener === 'function') media.removeEventListener('change', handle);
            else if (typeof media.removeListener === 'function') media.removeListener(handle);
        };
    }, []);

    return narrow;
}
