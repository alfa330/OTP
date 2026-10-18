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
 * вниз — возвращает.
 *
 * Чат из поиска по тексту открывается на найденном сообщении: focus (ref)
 * — { key, messageId, done(row|null) }. Тот же момент, что и для конца ленты,
 * только сообщение встаёт посередине окна и к концу лента не прилипает. Нет
 * сообщения в ленте (удалено) — done(null) и обычный конец. */
const END_GAP = 4;

export const messageRow = (box, messageId) => [...(box?.querySelectorAll('[data-message-id]') || [])]
    .find((node) => node.dataset.messageId === messageId) || null;

// Строка посередине окна ленты: scrollIntoView прокрутил бы заодно и страницу.
export const centerMessageRow = (box, row) => {
    const rowBox = row.getBoundingClientRect();
    const frame = box.getBoundingClientRect();
    box.scrollTop += rowBox.top - frame.top - Math.max(0, (box.clientHeight - rowBox.height) / 2);
};

export default function useThreadEnd(boxRef, { chatKey, loaded, focus = null }) {
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
        const target = focus?.current;
        if (target?.key === chatKey) {
            focus.current = null;
            const row = messageRow(box, target.messageId);
            if (row) {
                centerMessageRow(box, row);
                pin.current = box.scrollHeight - box.scrollTop - box.clientHeight <= END_GAP
                    ? { top: box.scrollTop } : null;
                target.done?.(row);
                return;
            }
            target.done?.(null);
        }
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
