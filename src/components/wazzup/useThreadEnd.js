import { useLayoutEffect, useRef } from 'react';

/* Лента открытого чата начинается с последних сообщений и держится на них,
 * пока человек сам не ушёл читать выше.
 *
 * В конец её ставит layout-эффект: React уже вставил сообщения, кадр ещё не
 * нарисован. Прежний requestAnimationFrame из ответа сервера мог сработать
 * раньше отрисовки — после долгой задачи вкладки браузер рисует кадр первым, —
 * и прокрутка ложилась на пустую ленту: чат открывался в самом верху.
 *
 * Высота меняется и после открытия: заметки, «Ответ не нужен» и поле ввода
 * приходят своими запросами. Пока человек у последних сообщений,
 * ResizeObserver возвращает его к ним; прокрутка вверх это снимает, возврат
 * вниз — возвращает. */
const END_GAP = 4;

export default function useThreadEnd(boxRef, { chatKey, loaded }) {
    // { top } — человек у последних сообщений; top — его прокрутка там.
    const pin = useRef(null);
    // Чат, лента которого грузится заново (открыли, «Обновить»): дождаться и встать в конец.
    const awaited = useRef('');
    const toEnd = (box) => {
        box.scrollTop = box.scrollHeight;
        pin.current = { top: box.scrollTop };
    };

    useLayoutEffect(() => {
        if (!loaded) { awaited.current = chatKey; return; }
        const box = boxRef.current;
        if (!box || awaited.current !== chatKey) return;
        awaited.current = '';
        toEnd(box);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [boxRef, chatKey, loaded]);

    useLayoutEffect(() => {
        const box = boxRef.current;
        if (!box) return undefined;
        const stick = () => { if (pin.current) toEnd(box); };
        const scrolled = () => {
            if (box.scrollHeight - box.scrollTop - box.clientHeight <= END_GAP) pin.current = { top: box.scrollTop };
            // Ушёл вверх сам. Лента, выросшая под ним, scrollTop не уменьшает — тогда догоняем.
            // Запас в пиксель — на дробную прокрутку при масштабе экрана.
            else if (pin.current && box.scrollTop < pin.current.top - 1) pin.current = null;
            else stick();
        };
        const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(stick) : null;
        observer?.observe(box);
        if (box.firstElementChild) observer?.observe(box.firstElementChild);
        box.addEventListener('scroll', scrolled, { passive: true });
        return () => {
            observer?.disconnect();
            box.removeEventListener('scroll', scrolled);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [boxRef, chatKey]);
}
