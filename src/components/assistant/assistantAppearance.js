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

export const assistantAppearanceKey = userId => (
    userId == null ? null : `otp:assistant:appearance:${userId}`
);

export function readAssistantEffect(key) {
    try {
        return key ? normalizeAssistantEffect(window.localStorage.getItem(key)) : DEFAULT_ASSISTANT_EFFECT;
    } catch {
        return DEFAULT_ASSISTANT_EFFECT;
    }
}
