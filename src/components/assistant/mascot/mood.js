/* Настроение обликов-талисманов помощника (тыква, котик). Только чистые
 * функции — их проверяют tests/assistant_pumpkin.test.mjs и
 * tests/assistant_cat.test.mjs; жесты шарика слушает useMascotMood.js.
 *
 * Порядок у всех талисманов общий, разные только эмоции (script):
 *   спокойно        → rest;    курсор над шариком → hover;
 *   нажатие         → press;   щелчок → следующая из череды cycle;
 *                     RAPID_CLICKS щелчков подряд → rapid;
 *   перетаскивание  → drag, отпустили → drop;
 *   закрыли помощника → close; SLEEP_AFTER_MS без движения → sleep (если
 *                     задана drowsy — сперва она на drowsyMs: котик зевает,
 *                     потом сворачивается клубочком); шевельнули мышь → wake.
 */

export const RAPID_CLICKS = 4;
export const RAPID_WINDOW_MS = 1600;
export const SLEEP_AFTER_MS = 45000;
/* Шарик открывается и закрывается на отпускании кнопки, а click приходит
   следом — уже после закрытия. Этот щелчок — тот же жест, что и закрытие:
   грусть он не перебивает и череду не двигает (но в счёт «шквала» идёт —
   при частых щелчках каждый второй закрывает помощника). */
export const CLOSE_CLICK_MS = 300;

const HOLD = {
    press: 650,
    click: 2400,
    rapid: 2600,
    drop: 1400,
    close: 1800,
    wake: 900,
};

export function createMascotMood(script) {
    const { rest, hover, press, cycle, rapid, drag, drop, close, sleep, wake, drowsy = null, drowsyMs = 0 } = script;

    const initialMood = () => ({
        base: rest, override: null, clicks: [], sleeping: false, sleptAt: 0, closedAt: Number.NEGATIVE_INFINITY, cycle: 0,
    });

    const hold = (state, emotion, until) => ({ ...state, override: { emotion, until } });

    const moodReducer = (state, event) => {
        const now = event.now;
        switch (event.type) {
            case 'enter':
                return { ...state, base: hover };
            case 'leave':
                return { ...state, base: rest };
            case 'press':
                return hold(state, press, now + HOLD.press);
            case 'click': {
                const clicks = [...state.clicks.filter((t) => now - t < RAPID_WINDOW_MS), now];
                if (clicks.length >= RAPID_CLICKS) {
                    return { ...hold(state, rapid, now + HOLD.rapid), clicks: [] };
                }
                // Щелчок, которым помощника закрыли (мышью — сразу после «закрыли», с
                // клавиатуры — помечен closing), в счёт «шквала» идёт, но грусть оставляет.
                if (event.closing || now - state.closedAt < CLOSE_CLICK_MS) return { ...state, clicks };
                const emotion = cycle[state.cycle % cycle.length];
                return { ...hold(state, emotion, now + HOLD.click), clicks, cycle: state.cycle + 1 };
            }
            case 'dragStart':
                return hold(state, drag, Number.POSITIVE_INFINITY);
            case 'dragEnd':
                return hold(state, drop, now + HOLD.drop);
            case 'close':
                return { ...hold(state, close, now + HOLD.close), closedAt: now };
            case 'sleep':
                // Таймер простоя присылает «сон» раз в секунду: уже спящего не трогаем —
                // иначе дремота котика начиналась бы заново и React перерисовывал бы зря.
                return state.sleeping ? state : { ...state, sleeping: true, sleptAt: now };
            case 'wake':
                return state.sleeping ? { ...hold(state, wake, now + HOLD.wake), sleeping: false } : state;
            default:
                return state;
        }
    };

    const displayEmotion = (state, now) => {
        if (state.sleeping) return drowsy && now - state.sleptAt < drowsyMs ? drowsy : sleep;
        if (state.override && now < state.override.until) return state.override.emotion;
        return state.base;
    };

    /* Когда показанная эмоция сменится сама, без жеста: гаснет временная или
       дремота переходит в сон. null — пока ничего не изменится. */
    const nextChangeAt = (state, now) => {
        if (state.sleeping) {
            return drowsy && now - state.sleptAt < drowsyMs ? state.sleptAt + drowsyMs : null;
        }
        const until = state.override?.until;
        return Number.isFinite(until) && until > now ? until : null;
    };

    return { initialMood, moodReducer, displayEmotion, nextChangeAt };
}

/* Куда смотрит талисман: смещение курсора от его центра → [-1, 1] по осям, мягко:
   рядом взгляд меняется быстро, вдали упирается в край. */
export const lookVector = (dx, dy, reach = 240) => {
    const soft = (v) => {
        const k = v / reach;
        return Number.isFinite(k) ? k / (1 + Math.abs(k)) * 2 : 0;
    };
    const clampUnit = (v) => Math.max(-1, Math.min(1, v));
    return { x: clampUnit(soft(dx)), y: clampUnit(soft(dy)) };
};
