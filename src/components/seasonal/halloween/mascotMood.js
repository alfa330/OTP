/* Хэллоуин-2026: настроение тыквы-помощника (по наброскам владельца «Тыква
 * (эмоции)», 30.10.2026). Только чистые функции — их проверяет
 * tests/halloween_mascot.test.mjs; сама тыква — PumpkinMascot.jsx.
 *
 *   спокойно        → обычная;   курсор над тыквой → радость;
 *   нажатие         → удивление; щелчок → следующая из череды (подмигнуть,
 *                     влюбиться, «круто», удивиться, обрадоваться, задуматься,
 *                     «нейтрально»); 4 щелчка подряд → злость;
 *   перетаскивание  → страх, отпустили → радость;
 *   закрыли помощника → грусть; 45 с без движения → сон, шевельнули мышь →
 *                     удивление и снова обычная.
 */

export const EMOTIONS = ['normal', 'joy', 'anger', 'sad', 'surprise', 'wink', 'sleep', 'confusion',
    'love', 'fear', 'cool', 'neutral'];

export const CLICK_CYCLE = ['wink', 'love', 'cool', 'surprise', 'joy', 'confusion', 'neutral'];
export const RAPID_CLICKS = 4;
export const RAPID_WINDOW_MS = 1600;
export const SLEEP_AFTER_MS = 45000;

const HOLD = {
    press: 650,
    click: 2400,
    anger: 2600,
    dragEnd: 1400,
    close: 1800,
    wake: 900,
};

export const initialMood = () => ({ base: 'normal', override: null, clicks: [], sleeping: false, cycle: 0 });

const hold = (state, emotion, until) => ({ ...state, override: { emotion, until } });

export const moodReducer = (state, event) => {
    const now = event.now;
    switch (event.type) {
        case 'enter':
            return { ...state, base: 'joy' };
        case 'leave':
            return { ...state, base: 'normal' };
        case 'press':
            return hold(state, 'surprise', now + HOLD.press);
        case 'click': {
            const clicks = [...state.clicks.filter((t) => now - t < RAPID_WINDOW_MS), now];
            if (clicks.length >= RAPID_CLICKS) {
                return { ...hold(state, 'anger', now + HOLD.anger), clicks: [] };
            }
            const emotion = CLICK_CYCLE[state.cycle % CLICK_CYCLE.length];
            return { ...hold(state, emotion, now + HOLD.click), clicks, cycle: state.cycle + 1 };
        }
        case 'dragStart':
            return hold(state, 'fear', Number.POSITIVE_INFINITY);
        case 'dragEnd':
            return hold(state, 'joy', now + HOLD.dragEnd);
        case 'close':
            return hold(state, 'sad', now + HOLD.close);
        case 'sleep':
            return { ...state, sleeping: true };
        case 'wake':
            return state.sleeping ? { ...hold(state, 'surprise', now + HOLD.wake), sleeping: false } : state;
        default:
            return state;
    }
};

export const displayEmotion = (state, now) => {
    if (state.sleeping) return 'sleep';
    if (state.override && now < state.override.until) return state.override.emotion;
    return state.base;
};

/* Куда смотрит тыква: смещение курсора от её центра → [-1, 1] по осям, мягко:
   рядом с тыквой взгляд меняется быстро, вдали упирается в край. */
export const lookVector = (dx, dy, reach = 240) => {
    const soft = (v) => {
        const k = v / reach;
        return Number.isFinite(k) ? k / (1 + Math.abs(k)) * 2 : 0;
    };
    const clampUnit = (v) => Math.max(-1, Math.min(1, v));
    return { x: clampUnit(soft(dx)), y: clampUnit(soft(dy)) };
};
