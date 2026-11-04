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

/* Облик помощника — один из эффектов знака iCORE или талисман с эмоциями: тыква
   (появилась к Хэллоуину-2026 и осталась на выбор) и котик (02.11.2026).
   Талисманы — не эффекты знака, поэтому в ICORE_ASSISTANT_EFFECTS их нет: тот
   список рисует знак и в лаборатории иконок. */
export const PUMPKIN_APPEARANCE = 'pumpkin';
export const CAT_APPEARANCE = 'cat';
export const MASCOT_APPEARANCES = [PUMPKIN_APPEARANCE, CAT_APPEARANCE];

export const ASSISTANT_APPEARANCES = [
    ...ICORE_ASSISTANT_EFFECTS,
    { id: PUMPKIN_APPEARANCE, number: '05', name: 'Тыква', caption: 'С характером', description: 'Следит за курсором и меняет эмоции: радуется наведению, удивляется нажатию, засыпает без дела.' },
    { id: CAT_APPEARANCE, number: '06', name: 'Котик', caption: 'Девять эмоций', description: 'Вислоухий котёнок: радуется наведению, пугается, когда шарик тащат, грустит, когда помощника закрыли, зевает и засыпает клубочком без дела.' },
];

/* Котик с 04.11.2026 — пока только владельцу (448) и Руслану (2). Остальным
   его нет в выборе, а выбранный раньше уступает знаку по умолчанию; сам выбор в
   хранилище не стирается — откроют котика всем, он вернётся без перевыбора. */
export const CAT_APPEARANCE_USER_IDS = new Set([2, 448]);

export const canUseAssistantAppearance = (value, userId) => (
    value !== CAT_APPEARANCE || CAT_APPEARANCE_USER_IDS.has(Number(userId))
);

export const assistantAppearancesFor = userId => (
    ASSISTANT_APPEARANCES.filter(item => canUseAssistantAppearance(item.id, userId))
);

export const normalizeAssistantAppearance = (value, userId) => (
    MASCOT_APPEARANCES.includes(value) && canUseAssistantAppearance(value, userId)
        ? value
        : normalizeAssistantEffect(value)
);

/* Хэллоуин: облик по умолчанию — тыква, а выбор этих дней хранится отдельно.
   Кто выбор не трогал, после праздника увидит свой прежний облик; кто выбрал в
   праздник (тыкву или знак) — тот выбор за ним и остаётся. */
const HALLOWEEN_SUFFIX = ':halloween-2026';

const APPEARANCE_KEY_PREFIX = 'otp:assistant:appearance:';

export const assistantAppearanceKey = userId => (
    userId == null ? null : `${APPEARANCE_KEY_PREFIX}${userId}`
);

// Ключ облика сам называет аккаунт: «otp:assistant:appearance:<id>[:праздник]».
const appearanceKeyUserId = key => String(key || '').slice(APPEARANCE_KEY_PREFIX.length).split(':')[0];

/* Какие талисманы грузить со стартом портала, чтобы облик встал в шарик без смены
   на глазах: выбранные в этом браузере (любым аккаунтом — ключ на каждого) и тыква
   в праздник. Праздничный выбор после праздника не в счёт — решает основной ключ.
   Закрытое хранилище (или его нет) — только праздничная тыква: остальной
   талисман подгрузится, когда его покажут. */
export function mascotsToPreload(storage, halloween = false) {
    const chosen = new Set(halloween ? [PUMPKIN_APPEARANCE] : []);
    try {
        for (let i = 0; i < storage.length; i += 1) {
            const key = storage.key(i) || '';
            if (!key.startsWith(APPEARANCE_KEY_PREFIX) || key.endsWith(HALLOWEEN_SUFFIX)) continue;
            const value = storage.getItem(key);
            if (MASCOT_APPEARANCES.includes(value) && canUseAssistantAppearance(value, appearanceKeyUserId(key))) {
                chosen.add(value);
            }
        }
    } catch {
        // см. выше
    }
    return chosen;
}

const fallback = halloween => (halloween ? PUMPKIN_APPEARANCE : DEFAULT_ASSISTANT_EFFECT);

export function readAssistantEffect(key, halloween = isHalloweenTime()) {
    try {
        if (!key) return fallback(halloween);
        const userId = appearanceKeyUserId(key);
        if (halloween) {
            const festive = window.localStorage.getItem(key + HALLOWEEN_SUFFIX);
            return festive ? normalizeAssistantAppearance(festive, userId) : PUMPKIN_APPEARANCE;
        }
        return normalizeAssistantAppearance(window.localStorage.getItem(key), userId);
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
