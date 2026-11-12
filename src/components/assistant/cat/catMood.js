/* Настроение облика помощника «Котик» (наброски владельца 02.11.2026: лист
 * из девяти эмоций и спящий клубочком котёнок). Порядок жестов общий для
 * талисманов — ../mascot/mood.js; здесь — какая поза котика на какой жест.
 * Проверяет tests/assistant_cat.test.mjs; сам котик — CatMascot.jsx.
 *
 *   спокойно        → сидит (normal), моргает и плавно ведёт головой за курсором
 *                     во все стороны (catLook.js); курсор замер — снова смотрит
 *                     на человека;
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

/* Кадры целиком — эмоции. Голова к курсору и моргание в спокойной позе — слоями
   (тело и голова под сеткой углов, catLook.js), отдельных кадров look-left, look-right
   и blink больше нет. */
export const FRAMES = [...EMOTIONS];

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

/* Моргает раз в 3–6 с, на BLINK_MS. */
export const BLINK_MS = 150;
export const nextBlinkDelay = (random = Math.random) => 3000 + Math.round(random() * 3000);
