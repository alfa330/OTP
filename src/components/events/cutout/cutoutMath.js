/*
 * Арифметика вырезания фона для «Доски почёта» — без DOM и без нейросети,
 * поэтому её проверяет Node (tests/honor_cutout.test.mjs).
 *
 * Модель — MODNet (портретная «матовая маска», лицензия Apache 2.0, см.
 * MODNET-LICENSE.txt). Её вход — картинка, у которой короткая сторона 512 px,
 * а обе стороны кратны 32, цвет нормирован в [-1, 1]; выход — прозрачность
 * каждой точки в [0, 1] того же размера.
 */

export const MODEL_SHORT_SIDE = 512;
const MODEL_STEP = 32;
// Длинная сторона кадра для модели — с потолком: панорама 4:1 дала бы вход
// 2048 × 512, а это лишние секунды и память без пользы для портрета.
const MODEL_LONG_SIDE_MAX = 1024;

const roundToStep = (value) => Math.max(MODEL_STEP, Math.round(value / MODEL_STEP) * MODEL_STEP);

/** Размер входа модели для снимка width × height: короткая сторона — 512,
    обе кратны 32, длинная — не больше 1024. */
export const modelInputSize = (width, height) => {
    const w = Math.max(1, Number(width) || 1);
    const h = Math.max(1, Number(height) || 1);
    const scale = Math.min(MODEL_SHORT_SIDE / Math.min(w, h), MODEL_LONG_SIDE_MAX / Math.max(w, h));
    return { width: roundToStep(w * scale), height: roundToStep(h * scale) };
};

/** Уменьшить width × height, чтобы длинная сторона была не больше maxSide
    (меньшее не увеличиваем). */
export const fitSize = (width, height, maxSide) => {
    const w = Math.max(1, Math.round(Number(width) || 1));
    const h = Math.max(1, Math.round(Number(height) || 1));
    const scale = Math.min(1, maxSide / Math.max(w, h));
    return { width: Math.max(1, Math.round(w * scale)), height: Math.max(1, Math.round(h * scale)) };
};

/** RGBA (как у getImageData) → вход модели: Float32Array [3, h, w],
    (канал / 255 − 0.5) / 0.5. */
export const toModelInput = (rgba, width, height) => {
    const plane = width * height;
    const out = new Float32Array(plane * 3);
    for (let i = 0; i < plane; i += 1) {
        const p = i * 4;
        out[i] = rgba[p] / 127.5 - 1;
        out[plane + i] = rgba[p + 1] / 127.5 - 1;
        out[2 * plane + i] = rgba[p + 2] / 127.5 - 1;
    }
    return out;
};

/** Маска модели [0, 1] → RGBA, где маска лежит в альфа-канале (цвет белый):
    такую картинку браузер растягивает до размера снимка и накладывает как
    маску (destination-in). */
export const matteToRGBA = (matte, width, height) => {
    const plane = width * height;
    const out = new Uint8ClampedArray(plane * 4);
    for (let i = 0; i < plane; i += 1) {
        const p = i * 4;
        out[p] = 255;
        out[p + 1] = 255;
        out[p + 2] = 255;
        const value = Number(matte[i]);
        out[p + 3] = value >= 1 ? 255 : value <= 0 || Number.isNaN(value) ? 0 : Math.round(value * 255);
    }
    return out;
};

/** Сколько снимка занимает человек: доля точек с заметной непрозрачностью. */
export const coverage = (rgba, width, height, threshold = 24) => {
    const plane = width * height;
    if (!plane) return 0;
    let count = 0;
    for (let i = 0; i < plane; i += 1) if (rgba[i * 4 + 3] > threshold) count += 1;
    return count / plane;
};

/** Рамка непрозрачной части RGBA: {left, top, right, bottom} (right и bottom —
    НЕ включительно) или null, если снимок пуст. Полупрозрачную дымку краёв
    (до threshold из 255) рамка не считает. */
export const alphaBounds = (rgba, width, height, threshold = 24) => {
    let left = width;
    let top = height;
    let right = -1;
    let bottom = -1;
    for (let y = 0; y < height; y += 1) {
        const row = y * width * 4;
        for (let x = 0; x < width; x += 1) {
            if (rgba[row + x * 4 + 3] > threshold) {
                if (x < left) left = x;
                if (x > right) right = x;
                if (y < top) top = y;
                bottom = y;
            }
        }
    }
    if (right < 0) return null;
    return { left, top, right: right + 1, bottom: bottom + 1 };
};

/** Рамка обрезки вокруг человека: немного воздуха сбоку и сверху (доля
    высоты фигуры), снизу — ни точки: фигура на доске стоит на нижнем крае
    карточки, и «пол» под ней дал бы щель. */
export const cropAround = (bounds, width, height, { side = 0.03, top = 0.03 } = {}) => {
    if (!bounds) return null;
    const figureHeight = bounds.bottom - bounds.top;
    const padSide = Math.round(figureHeight * side);
    const padTop = Math.round(figureHeight * top);
    const left = Math.max(0, bounds.left - padSide);
    const right = Math.min(width, bounds.right + padSide);
    const topEdge = Math.max(0, bounds.top - padTop);
    return { left, top: topEdge, width: right - left, height: bounds.bottom - topEdge };
};
