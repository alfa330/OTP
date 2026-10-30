import { isHalloweenTime } from '../seasonal/halloween/halloweenWindow.js';

export const DEFAULT_ASSISTANT_EFFECT = 'aurora';

export const ICORE_ASSISTANT_EFFECTS = [
    { id: 'glass', number: '01', name: 'Чистое стекло', caption: 'Тихий перелив', description: 'Прозрачное стекло с холодной кромкой и мягким скользящим бликом.' },
    { id: 'aurora', number: '02', name: 'Северное сияние', caption: 'Мягкое свечение', description: 'Мятный, голубой и сиреневый свет медленно перетекают внутри знака.' },
    { id: 'flow', number: '03', name: 'Световой поток', caption: 'Живая энергия', description: 'Тонкие синие и фиолетовые волны проходят сквозь прозрачную форму.' },
    { id: 'dust', number: '04', name: 'Звёздная пыль', caption: 'Глубина и искры', description: 'Редкие светящиеся частицы плывут в полупрозрачном индиго.' },
];

export const normalizeAssistantEffect = value => (
    ICORE_ASSISTANT_EFFECTS.some(item => item.id === value) ? value : DEFAULT_ASSISTANT_EFFECT
);

/* Облик помощника — один из эффектов знака iCORE или тыква с эмоциями (появилась
   к Хэллоуину-2026 и осталась на выбор). Тыква — не эффект знака, поэтому в
   ICORE_ASSISTANT_EFFECTS её нет: тот список рисует знак и в лаборатории иконок. */
export const PUMPKIN_APPEARANCE = 'pumpkin';

export const ASSISTANT_APPEARANCES = [
    ...ICORE_ASSISTANT_EFFECTS,
    { id: PUMPKIN_APPEARANCE, number: '05', name: 'Тыква', caption: 'С характером', description: 'Следит за курсором и меняет эмоции: радуется наведению, удивляется нажатию, засыпает без дела.' },
];

export const normalizeAssistantAppearance = value => (
    value === PUMPKIN_APPEARANCE ? value : normalizeAssistantEffect(value)
);

export const assistantAppearanceKey = userId => (
    userId == null ? null : `otp:assistant:appearance:${userId}`
);

/* Хэллоуин: облик по умолчанию — тыква, а выбор этих дней хранится отдельно.
   Кто выбор не трогал, после праздника увидит свой прежний облик; кто выбрал в
   праздник (тыкву или знак) — тот выбор за ним и остаётся. */
const HALLOWEEN_SUFFIX = ':halloween-2026';
const fallback = halloween => (halloween ? PUMPKIN_APPEARANCE : DEFAULT_ASSISTANT_EFFECT);

export function readAssistantEffect(key, halloween = isHalloweenTime()) {
    try {
        if (!key) return fallback(halloween);
        if (halloween) {
            const festive = window.localStorage.getItem(key + HALLOWEEN_SUFFIX);
            return festive ? normalizeAssistantAppearance(festive) : PUMPKIN_APPEARANCE;
        }
        return normalizeAssistantAppearance(window.localStorage.getItem(key));
    } catch {
        return fallback(halloween);
    }
}

export function storeAssistantEffect(key, effect, halloween = isHalloweenTime()) {
    if (!key) return;
    window.localStorage.setItem(key, effect);
    if (halloween) window.localStorage.setItem(key + HALLOWEEN_SUFFIX, effect);
}

export const isAssistantAppearanceKey = (storageKey, key) => (
    Boolean(key) && (storageKey === key || storageKey === key + HALLOWEEN_SUFFIX)
);
