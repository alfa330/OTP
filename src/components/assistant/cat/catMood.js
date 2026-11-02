/* Настроение облика помощника «Котик» (наброски владельца 02.11.2026: лист
 * из девяти эмоций и спящий клубочком котёнок). Порядок жестов общий для
 * талисманов — ../mascot/mood.js; здесь — какая поза котика на какой жест.
 * Проверяет tests/assistant_cat.test.mjs; сам котик — CatMascot.jsx.
 *
 *   спокойно        → сидит (normal), моргает, поворачивает голову к курсору;
 *   курсор над котиком → Happy;  нажатие → Surprised;
 *   щелчок          → по кругу: Excited, Mischievous, Confused, Happy, Surprised;
 *                     4 щелчка подряд → Angry;
 *   перетаскивание  → Scared, отпустили → Happy;
 *   закрыли помощника → Sad;
 *   45 с без движения → зевает (Sleepy), через DROWSY_MS засыпает клубочком
 *                     (asleep); шевельнули мышь → Surprised и снова normal.
 */
import { createMascotMood } from '../mascot/mood.js';

export const EMOTIONS = ['normal', 'happy', 'excited', 'sad', 'angry', 'surprised', 'scared', 'sleepy',
    'confused', 'mischievous', 'asleep'];

/* Кадры — эмоции плюс спокойная поза с головой к курсору и моргание. */
export const FRAMES = [...EMOTIONS, 'look-left', 'look-right', 'blink'];

export const CLICK_CYCLE = ['excited', 'mischievous', 'confused', 'happy', 'surprised'];
export const DROWSY_MS = 4000;

const catMood = createMascotMood({
    rest: 'normal',
    hover: 'happy',
    press: 'surprised',
    cycle: CLICK_CYCLE,
    rapid: 'angry',
    drag: 'scared',
    drop: 'happy',
    close: 'sad',
    drowsy: 'sleepy',
    drowsyMs: DROWSY_MS,
    sleep: 'asleep',
    wake: 'surprised',
});

export const { initialMood, moodReducer, displayEmotion, nextChangeAt } = catMood;
export default catMood;

/* Голова к курсору: дальше LOOK_TURN — повернуть, ближе LOOK_BACK — снова
   прямо. Зазор между порогами — чтобы на границе голова не дёргалась. */
export const LOOK_TURN = 0.5;
export const LOOK_BACK = 0.3;

export const lookSide = (current, x) => {
    if (x >= LOOK_TURN) return 'right';
    if (x <= -LOOK_TURN) return 'left';
    if (Math.abs(x) <= LOOK_BACK) return 'center';
    return current;
};

/* Какой кадр показать: в спокойной позе — поворот головы или моргание
   (моргает только глядя прямо — кадр моргания смотрит прямо). */
export const frameFor = (emotion, side, blink) => {
    if (emotion !== 'normal') return emotion;
    if (side !== 'center') return `look-${side}`;
    return blink ? 'blink' : 'normal';
};

/* Моргает раз в 3–6 с, на BLINK_MS. */
export const BLINK_MS = 150;
export const nextBlinkDelay = (random = Math.random) => 3000 + Math.round(random() * 3000);
