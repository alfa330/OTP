import { useEffect, useReducer, useRef, useState } from 'react';
import { listenToOrb } from './orbGestures.js';

/* Настроение талисмана в шарике (тыква, котик). Жесты шарика слушает
 * listenToOrb (orbGestures.js), эмоции считает редьюсер облика (mood.js).
 * Возвращает эмоцию, которую показать сейчас.
 */
export default function useMascotMood(rootRef, mood, { interactive = true, onLook } = {}) {
    const [state, dispatch] = useReducer(mood.moodReducer, undefined, mood.initialMood);
    const [, setTick] = useState(0);
    const lookRef = useRef(onLook);
    lookRef.current = onLook;
    const sleepingRef = useRef(state.sleeping);
    sleepingRef.current = state.sleeping;

    /* Эмоция меняется сама (гаснет временная, дремота переходит в сон) —
       перерисовка ровно в срок. */
    useEffect(() => {
        const at = mood.nextChangeAt(state, Date.now());
        if (at == null) return undefined;
        const timer = window.setTimeout(() => setTick((n) => n + 1), Math.max(0, at - Date.now()) + 16);
        return () => window.clearTimeout(timer);
    }, [mood, state]);

    useEffect(() => {
        const root = rootRef.current;
        if (!root || !interactive) return undefined;
        return listenToOrb(root, {
            dispatch,
            isSleeping: () => sleepingRef.current,
            onLook: (x, y) => lookRef.current?.(x, y),
        });
    }, [rootRef, interactive]);

    return mood.displayEmotion(state, Date.now());
}
