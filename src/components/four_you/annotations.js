// Общий контракт совместной разметки 4 You.
// Координаты всех элементов нормализованы к фото (0..1), поэтому рисунок,
// стикеры и текст одинаково ложатся на превью, полноразмерное фото и зум.

export const FOUR_YOU_USER_NAMES = { 2: 'Руслан', 241: 'Адия' };
export const userName = (id) => FOUR_YOU_USER_NAMES[Number(id)] || 'Гость';

// Ключи шрифтов (должны совпадать с FOUR_YOU_ANN_FONTS на бэкенде).
export const FONTS = [
    { key: 'inter', label: 'Inter', css: 'Inter, ui-sans-serif, system-ui, -apple-system, sans-serif' },
    { key: 'script', label: 'Рукопись', css: '"Segoe Script", "Bradley Hand", "Brush Script MT", cursive' },
    { key: 'serif', label: 'Серив', css: 'Georgia, "Times New Roman", serif' },
    { key: 'mono', label: 'Моно', css: '"JetBrains Mono", "Courier New", ui-monospace, monospace' },
    { key: 'display', label: 'Жирный', css: '"Arial Black", Impact, system-ui, sans-serif' },
    { key: 'round', label: 'Округлый', css: '"Comic Sans MS", "Chalkboard SE", "Segoe UI", sans-serif' },
];
export const fontCss = (key) => (FONTS.find((f) => f.key === key) || FONTS[0]).css;

// Ключи фонов (должны совпадать с FOUR_YOU_ANN_BACKGROUNDS на бэкенде).
export const BACKGROUNDS = [
    { key: 'none', label: 'Без фона', swatch: 'linear-gradient(135deg,#f3f3f1,#e9e9e6)' },
    { key: 'hearts', label: 'Сердечки', swatch: 'linear-gradient(135deg,#ffd9e6,#ff8fb8)' },
    { key: 'aurora', label: 'Аврора', swatch: 'linear-gradient(135deg,#7ef0d0,#7aa8ff,#c084fc)' },
    { key: 'bokeh', label: 'Боке', swatch: 'linear-gradient(135deg,#2a2350,#6d4dd6)' },
    { key: 'stars', label: 'Звёзды', swatch: 'linear-gradient(135deg,#0b1030,#26306b)' },
    { key: 'sunset', label: 'Закат', swatch: 'linear-gradient(135deg,#ff8a5c,#ff5f9e,#7a4dff)' },
];

export const EMOJI = [
    '❤️', '💕', '💖', '💗', '😍', '🥰', '😘', '😎', '🥹', '😇', '🤍', '💛',
    '💋', '🌹', '🌸', '🌺', '✨', '⭐', '🌈', '🔥', '🎉', '🥳', '🎂', '💐',
    '🫶', '👑', '🦋', '🍓', '🍰', '☀️', '🌙', '💎',
];

export const COLORS = [
    '#ffffff', '#111111', '#ff3b6b', '#ff7ab8', '#ffd23f',
    '#3ddc97', '#36c5f0', '#a06bff', '#ff8a3d', '#000000',
];

export const emptyAnnotations = () => ({
    strokes: [], stickers: [], texts: [], comments: [], background: 'none',
});

// Терпимая нормализация (бэкенд уже санитайзит, но данные могут быть старыми/битыми).
export const normalizeAnnotations = (raw) => {
    const a = raw && typeof raw === 'object' ? raw : {};
    return {
        strokes: Array.isArray(a.strokes) ? a.strokes : [],
        stickers: Array.isArray(a.stickers) ? a.stickers : [],
        texts: Array.isArray(a.texts) ? a.texts : [],
        comments: Array.isArray(a.comments) ? a.comments : [],
        background: typeof a.background === 'string' ? a.background : 'none',
        updated_by: a.updated_by,
    };
};

export const hasVisibleAnnotations = (a) => !!a && (
    (a.strokes && a.strokes.length > 0)
    || (a.stickers && a.stickers.length > 0)
    || (a.texts && a.texts.length > 0)
    || (a.background && a.background !== 'none')
);

export const annotationsCount = (a) => {
    if (!a) return 0;
    return (a.strokes?.length || 0) + (a.stickers?.length || 0)
        + (a.texts?.length || 0) + (a.comments?.length || 0)
        + (a.background && a.background !== 'none' ? 1 : 0);
};
