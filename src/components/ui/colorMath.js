/*
 * Счёт цвета для IosColorPicker: перевод HEX ↔ RGB ↔ HSV и сетка готовых
 * оттенков. Без React — модуль целиком проверяет node --test
 * (tests/color_math.test.mjs).
 *
 * HSV, а не HSL: квадрат спектра — это насыщенность по горизонтали и яркость
 * по вертикали при выбранном тоне (так устроены пикеры macOS и iOS), и в HSV
 * эти оси независимы: двигая точку вверх-вниз, человек меняет только яркость.
 */

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/* '#ffd400', 'FFD400', '#fd4' → '#FFD400'; не цвет → ''. */
export const normalizeHex = (text) => {
  let value = String(text || '').trim().replace(/^#/, '').toUpperCase();
  if (/^[0-9A-F]{3}$/.test(value)) value = value.split('').map((char) => char + char).join('');
  return /^[0-9A-F]{6}$/.test(value) ? `#${value}` : '';
};

export const hexToRgb = (hex) => {
  const value = normalizeHex(hex);
  if (!value) return null;
  return {
    r: parseInt(value.slice(1, 3), 16),
    g: parseInt(value.slice(3, 5), 16),
    b: parseInt(value.slice(5, 7), 16),
  };
};

export const rgbToHex = ({ r, g, b }) => `#${[r, g, b]
  .map((channel) => clamp(Math.round(channel), 0, 255).toString(16).padStart(2, '0'))
  .join('')
  .toUpperCase()}`;

/* h — 0…360, s и v — 0…1. */
export const rgbToHsv = ({ r, g, b }) => {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const delta = max - min;
  let h = 0;
  if (delta) {
    if (max === rn) h = ((gn - bn) / delta) % 6;
    else if (max === gn) h = (bn - rn) / delta + 2;
    else h = (rn - gn) / delta + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  return { h, s: max ? delta / max : 0, v: max };
};

export const hsvToRgb = ({ h, s, v }) => {
  const hue = ((h % 360) + 360) % 360;
  const c = v * s;
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
  const m = v - c;
  const [r, g, b] = hue < 60 ? [c, x, 0]
    : hue < 120 ? [x, c, 0]
      : hue < 180 ? [0, c, x]
        : hue < 240 ? [0, x, c]
          : hue < 300 ? [x, 0, c]
            : [c, 0, x];
  return { r: (r + m) * 255, g: (g + m) * 255, b: (b + m) * 255 };
};

export const hexToHsv = (hex) => {
  const rgb = hexToRgb(hex);
  return rgb ? rgbToHsv(rgb) : null;
};

export const hsvToHex = (hsv) => rgbToHex(hsvToRgb(hsv));

/* Светлый ли цвет — по воспринимаемой яркости (WCAG). На светлом образце
   галочка тёмная, на тёмном — белая. */
export const isLightColor = (hex) => {
  const rgb = hexToRgb(hex);
  if (!rgb) return true;
  const lin = (channel) => {
    const value = channel / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  const luminance = 0.2126 * lin(rgb.r) + 0.7152 * lin(rgb.g) + 0.0722 * lin(rgb.b);
  return luminance > 0.45;
};

const hslToHex = (h, s, l) => {
  const a = s * Math.min(l, 1 - l);
  const channel = (n) => {
    const k = (n + h / 30) % 12;
    return (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))) * 255;
  };
  return rgbToHex({ r: channel(0), g: channel(8), b: channel(4) });
};

/* Сетка как в пикере iOS: 12 столбцов × 10 строк. Первая строка — серые от
   белого к чёрному, дальше двенадцать тонов по кругу через 30° и девять
   ступеней от тёмного к светлому. 120 образцов покрывают любой «примерно
   такой цвет», а точный тон доводят в спектре. */
export const GRID_COLUMNS = 12;
const GRID_HUES = [0, 30, 45, 60, 90, 140, 175, 200, 220, 260, 290, 330];
const GRID_STEPS = [
  { s: 0.9, l: 0.16 },
  { s: 0.88, l: 0.24 },
  { s: 0.86, l: 0.32 },
  { s: 0.84, l: 0.4 },
  { s: 0.82, l: 0.5 },
  { s: 0.84, l: 0.6 },
  { s: 0.86, l: 0.7 },
  { s: 0.88, l: 0.8 },
  { s: 0.9, l: 0.89 },
];

export const gridPalette = () => {
  const rows = [];
  rows.push(Array.from({ length: GRID_COLUMNS }, (_, index) => {
    const level = 255 - Math.round((255 * index) / (GRID_COLUMNS - 1));
    return rgbToHex({ r: level, g: level, b: level });
  }));
  GRID_STEPS.forEach(({ s, l }) => {
    rows.push(GRID_HUES.map((hue) => hslToHex(hue, s, l)));
  });
  return rows;
};
