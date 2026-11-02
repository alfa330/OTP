/* Настроение облика помощника «Тыква» (по наброскам владельца «Тыква
 * (эмоции)», 30.10.2026). Порядок жестов общий для талисманов —
 * ../mascot/mood.js; здесь только какие лица у тыквы на какой жест.
 * Проверяет tests/assistant_pumpkin.test.mjs; сама тыква — PumpkinMascot.jsx.
 *
 *   спокойно        → обычная;   курсор над тыквой → радость;
 *   нажатие         → удивление; щелчок → следующая из череды (подмигнуть,
 *                     влюбиться, «круто», удивиться, обрадоваться, задуматься,
 *                     «нейтрально»); 4 щелчка подряд → злость;
 *   перетаскивание  → страх, отпустили → радость;
 *   закрыли помощника → грусть; 45 с без движения → сон, шевельнули мышь →
 *                     удивление и снова обычная.
 */
import { createMascotMood } from '../mascot/mood.js';

export { RAPID_CLICKS, RAPID_WINDOW_MS, SLEEP_AFTER_MS, lookVector } from '../mascot/mood.js';

export const EMOTIONS = ['normal', 'joy', 'anger', 'sad', 'surprise', 'wink', 'sleep', 'confusion',
    'love', 'fear', 'cool', 'neutral'];

export const CLICK_CYCLE = ['wink', 'love', 'cool', 'surprise', 'joy', 'confusion', 'neutral'];

const pumpkinMood = createMascotMood({
    rest: 'normal',
    hover: 'joy',
    press: 'surprise',
    cycle: CLICK_CYCLE,
    rapid: 'anger',
    drag: 'fear',
    drop: 'joy',
    close: 'sad',
    sleep: 'sleep',
    wake: 'surprise',
});

export const { initialMood, moodReducer, displayEmotion, nextChangeAt } = pumpkinMood;
export default pumpkinMood;
