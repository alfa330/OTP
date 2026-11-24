/*
 * Чтение штрихкода с кадра камеры — без сторонних библиотек (владелец 24.11.2026: «находить ОС,
 * просто отсканировав штрихкод»). Где браузер умеет сам (BarcodeDetector: Chrome на Android и
 * macOS), сканер берёт его; здесь — запасной путь для iPhone и Windows, где своего нет.
 *
 * Что читается — то, что клеят на ОС:
 *   EAN-13 / UPC-A — внутренние коды учёта («2000000056784» на наклейке бухгалтерии);
 *   EAN-8;
 *   Code 128 — серийный номер и SKU на заводской этикетке («K2201N0012345»).
 *
 * Как: по кадру проводится пачка линий (горизонтальные и под небольшим наклоном), вдоль каждой —
 * яркость, местный порог (середина между тёмным и светлым рядом), границы полос с долей пикселя
 * (так читается и полоса в 2 пикселя), ширины полос. Дальше — шаблоны символов с допуском и
 * контрольная цифра: EAN и Code 128 её несут, так что случайный мусор не проходит.
 */

/* ── EAN ─────────────────────────────────────────────────────────────────── */
// Ширины «пробел-штрих-пробел-штрих» цифры набора L (нечётный); G — те же в обратном порядке,
// R (правая половина) — те же ширины, но начиная со штриха.
const EAN_L = [[3, 2, 1, 1], [2, 2, 2, 1], [2, 1, 2, 2], [1, 4, 1, 1], [1, 1, 3, 2],
    [1, 2, 3, 1], [1, 1, 1, 4], [1, 3, 1, 2], [1, 2, 1, 3], [3, 1, 1, 2]];
const EAN_G = EAN_L.map((pattern) => [...pattern].reverse());
const EAN_LG = [...EAN_L, ...EAN_G];
// Первая цифра EAN-13 — по тому, какие из шести левых цифр набраны L, какие G.
const EAN_FIRST = ['LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG', 'LGGLLG', 'LGGGLG', 'LGLGLG', 'LGLGGL', 'LGGLGL'];

/* ── Code 128 ────────────────────────────────────────────────────────────── */
// 106 символов по 6 ширин (11 модулей), последний — STOP из 7 ширин (13 модулей).
export const C128 = [
    [2, 1, 2, 2, 2, 2], [2, 2, 2, 1, 2, 2], [2, 2, 2, 2, 2, 1], [1, 2, 1, 2, 2, 3], [1, 2, 1, 3, 2, 2],
    [1, 3, 1, 2, 2, 2], [1, 2, 2, 2, 1, 3], [1, 2, 2, 3, 1, 2], [1, 3, 2, 2, 1, 2], [2, 2, 1, 2, 1, 3],
    [2, 2, 1, 3, 1, 2], [2, 3, 1, 2, 1, 2], [1, 1, 2, 2, 3, 2], [1, 2, 2, 1, 3, 2], [1, 2, 2, 2, 3, 1],
    [1, 1, 3, 2, 2, 2], [1, 2, 3, 1, 2, 2], [1, 2, 3, 2, 2, 1], [2, 2, 3, 2, 1, 1], [2, 2, 1, 1, 3, 2],
    [2, 2, 1, 2, 3, 1], [2, 1, 3, 2, 1, 2], [2, 2, 3, 1, 1, 2], [3, 1, 2, 1, 3, 1], [3, 1, 1, 2, 2, 2],
    [3, 2, 1, 1, 2, 2], [3, 2, 1, 2, 2, 1], [3, 1, 2, 2, 1, 2], [3, 2, 2, 1, 1, 2], [3, 2, 2, 2, 1, 1],
    [2, 1, 2, 1, 2, 3], [2, 1, 2, 3, 2, 1], [2, 3, 2, 1, 2, 1], [1, 1, 1, 3, 2, 3], [1, 3, 1, 1, 2, 3],
    [1, 3, 1, 3, 2, 1], [1, 1, 2, 3, 1, 3], [1, 3, 2, 1, 1, 3], [1, 3, 2, 3, 1, 1], [2, 1, 1, 3, 1, 3],
    [2, 3, 1, 1, 1, 3], [2, 3, 1, 3, 1, 1], [1, 1, 2, 1, 3, 3], [1, 1, 2, 3, 3, 1], [1, 3, 2, 1, 3, 1],
    [1, 1, 3, 1, 2, 3], [1, 1, 3, 3, 2, 1], [1, 3, 3, 1, 2, 1], [3, 1, 3, 1, 2, 1], [2, 1, 1, 3, 3, 1],
    [2, 3, 1, 1, 3, 1], [2, 1, 3, 1, 1, 3], [2, 1, 3, 3, 1, 1], [2, 1, 3, 1, 3, 1], [3, 1, 1, 1, 2, 3],
    [3, 1, 1, 3, 2, 1], [3, 3, 1, 1, 2, 1], [3, 1, 2, 1, 1, 3], [3, 1, 2, 3, 1, 1], [3, 3, 2, 1, 1, 1],
    [3, 1, 4, 1, 1, 1], [2, 2, 1, 4, 1, 1], [4, 3, 1, 1, 1, 1], [1, 1, 1, 2, 2, 4], [1, 1, 1, 4, 2, 2],
    [1, 2, 1, 1, 2, 4], [1, 2, 1, 4, 2, 1], [1, 4, 1, 1, 2, 2], [1, 4, 1, 2, 2, 1], [1, 1, 2, 2, 1, 4],
    [1, 1, 2, 4, 1, 2], [1, 2, 2, 1, 1, 4], [1, 2, 2, 4, 1, 1], [1, 4, 2, 1, 1, 2], [1, 4, 2, 2, 1, 1],
    [2, 4, 1, 2, 1, 1], [2, 2, 1, 1, 1, 4], [4, 1, 3, 1, 1, 1], [2, 4, 1, 1, 1, 2], [1, 3, 4, 1, 1, 1],
    [1, 1, 1, 2, 4, 2], [1, 2, 1, 1, 4, 2], [1, 2, 1, 2, 4, 1], [1, 1, 4, 2, 1, 2], [1, 2, 4, 1, 1, 2],
    [1, 2, 4, 2, 1, 1], [4, 1, 1, 2, 1, 2], [4, 2, 1, 1, 1, 2], [4, 2, 1, 2, 1, 1], [2, 1, 2, 1, 4, 1],
    [2, 1, 4, 1, 2, 1], [4, 1, 2, 1, 2, 1], [1, 1, 1, 1, 4, 3], [1, 1, 1, 3, 4, 1], [1, 3, 1, 1, 4, 1],
    [1, 1, 4, 1, 1, 3], [1, 1, 4, 3, 1, 1], [4, 1, 1, 1, 1, 3], [4, 1, 1, 3, 1, 1], [1, 1, 3, 1, 4, 1],
    [1, 1, 4, 1, 3, 1], [3, 1, 1, 1, 4, 1], [4, 1, 1, 1, 3, 1], [2, 1, 1, 4, 1, 2], [2, 1, 1, 2, 1, 4],
    [2, 1, 1, 2, 3, 2],
];
export const C128_STOP = [2, 3, 3, 1, 1, 1, 2];
const C128_START = { 103: 'A', 104: 'B', 105: 'C' };

/* ── Сопоставление ширин с шаблоном ──────────────────────────────────────── */
/* Ширины приводятся к числу модулей шаблона (по их же сумме — так прощается и перспектива), и
   считается отклонение: в среднем и у худшей полосы. Пороги — как у ZXing (0,25/0,7), чуть шире:
   кадр с телефона мягче печатной ленты. */
const MAX_AVG = 0.32;
const MAX_ONE = 0.85;
const variance = (widths, pattern, modules) => {
    let total = 0;
    for (let index = 0; index < widths.length; index += 1) total += widths[index];
    if (total <= 0) return Infinity;
    const scale = modules / total;
    let sum = 0;
    for (let index = 0; index < widths.length; index += 1) {
        const diff = Math.abs(widths[index] * scale - pattern[index]);
        if (diff > MAX_ONE) return Infinity;
        sum += diff;
    }
    return sum / widths.length;
};
const bestOf = (widths, patterns, modules) => {
    let best = -1;
    let bestScore = MAX_AVG;
    for (let index = 0; index < patterns.length; index += 1) {
        const score = variance(widths, patterns[index], modules);
        if (score < bestScore) { bestScore = score; best = index; }
    }
    return best;
};

const sumRange = (runs, from, count) => {
    let total = 0;
    for (let index = from; index < from + count; index += 1) total += runs[index];
    return total;
};

export const eanCheckOk = (digits) => {
    if (!/^\d+$/.test(digits) || (digits.length !== 13 && digits.length !== 8)) return false;
    let sum = 0;
    const body = digits.slice(0, -1);
    // Справа налево: ближайшая к контрольной цифра — с весом 3.
    for (let index = 0; index < body.length; index += 1) {
        const digit = Number(body[body.length - 1 - index]);
        sum += index % 2 === 0 ? digit * 3 : digit;
    }
    return (10 - (sum % 10)) % 10 === Number(digits[digits.length - 1]);
};

/* runs[at] — тёмная полоса. guards — индексы элементов охранных групп (их полосы — по модулю). */
const guardsOk = (runs, at, count, modules, guards) => {
    const unit = sumRange(runs, at, count) / modules;
    for (const index of guards) {
        const ratio = runs[at + index] / unit;
        if (ratio < 0.4 || ratio > 1.9) return 0;
    }
    return unit;
};
const quietBefore = (runs, at, unit, need) => (at === 0 ? true : runs[at - 1] >= unit * need);

const decodeEan13 = (runs, at) => {
    if (at + 59 > runs.length) return null;
    const unit = guardsOk(runs, at, 59, 95, [0, 1, 2, 27, 28, 29, 30, 31, 56, 57, 58]);
    if (!unit || !quietBefore(runs, at, unit, 3) || (at + 59 < runs.length && runs[at + 59] < unit * 3)) return null;
    let digits = '';
    let parity = '';
    for (let digit = 0; digit < 6; digit += 1) {
        const found = bestOf(runs.slice(at + 3 + digit * 4, at + 7 + digit * 4), EAN_LG, 7);
        if (found < 0) return null;
        digits += String(found % 10);
        parity += found < 10 ? 'L' : 'G';
    }
    for (let digit = 0; digit < 6; digit += 1) {
        const found = bestOf(runs.slice(at + 32 + digit * 4, at + 36 + digit * 4), EAN_L, 7);
        if (found < 0) return null;
        digits += String(found);
    }
    const first = EAN_FIRST.indexOf(parity);
    if (first < 0) return null;
    const text = String(first) + digits;
    return eanCheckOk(text) ? { format: 'ean_13', text } : null;
};

const decodeEan8 = (runs, at) => {
    if (at + 43 > runs.length) return null;
    const unit = guardsOk(runs, at, 43, 67, [0, 1, 2, 19, 20, 21, 22, 23, 40, 41, 42]);
    if (!unit || !quietBefore(runs, at, unit, 3) || (at + 43 < runs.length && runs[at + 43] < unit * 3)) return null;
    let text = '';
    for (let digit = 0; digit < 4; digit += 1) {
        const found = bestOf(runs.slice(at + 3 + digit * 4, at + 7 + digit * 4), EAN_L, 7);
        if (found < 0) return null;
        text += String(found);
    }
    for (let digit = 0; digit < 4; digit += 1) {
        const found = bestOf(runs.slice(at + 24 + digit * 4, at + 28 + digit * 4), EAN_L, 7);
        if (found < 0) return null;
        text += String(found);
    }
    return eanCheckOk(text) ? { format: 'ean_8', text } : null;
};

/* Значения Code 128 → текст. Наборы: A — заглавные и управляющие, B — весь печатный ASCII, C — пары
   цифр. Переключения: 99 → C, 100 → B, 101 → A, 98 — один знак из соседнего набора. FNC — пропуск. */
export const code128Text = (values, startSet) => {
    let set = startSet;
    let shift = false;
    let text = '';
    for (const value of values) {
        const current = shift ? (set === 'A' ? 'B' : 'A') : set;
        shift = false;
        if (current === 'C') {
            if (value < 100) text += String(value).padStart(2, '0');
            else if (value === 100) set = 'B';
            else if (value === 101) set = 'A';
            else if (value !== 102) return null;
            continue;
        }
        if (value < 64) text += String.fromCharCode(value + 32);
        else if (value < 96) text += current === 'B' ? String.fromCharCode(value + 32) : String.fromCharCode(value - 64);
        else if (value === 98) shift = true;
        else if (value === 99) set = 'C';
        else if (value === 100) { if (current === 'A') set = 'B'; }
        else if (value === 101) { if (current === 'B') set = 'A'; }
        else if (value > 102) return null;
    }
    return text;
};

const C128_STARTS = [C128[103], C128[104], C128[105]];

/* values — [старт, данные…, контрольный]: сходится ли контрольный символ и какой выходит текст. */
const code128Finish = (values) => {
    const data = values.slice(1);
    if (data.length < 2) return null;
    const check = data.pop();
    let sum = values[0];
    data.forEach((value, position) => { sum += value * (position + 1); });
    if (sum % 103 !== check) return null;
    const text = code128Text(data, C128_START[values[0]]);
    return text ? { format: 'code_128', text } : null;
};

const decodeCode128 = (runs, at) => {
    if (at + 6 + 7 > runs.length) return null;
    const start = bestOf(runs.slice(at, at + 6), C128_STARTS, 11);
    if (start < 0) return null;
    const unit = sumRange(runs, at, 6) / 11;
    if (!quietBefore(runs, at, unit, 4)) return null;
    const values = [start + 103];
    let index = at + 6;
    while (index + 7 <= runs.length) {
        // STOP похож на начало обычного символа — принимаем его, только если сошлась контрольная сумма.
        if (variance(runs.slice(index, index + 7), C128_STOP, 13) < MAX_AVG) {
            const done = code128Finish(values);
            if (done && (index + 7 >= runs.length || runs[index + 7] >= unit * 3)) return done;
        }
        const found = bestOf(runs.slice(index, index + 6), C128, 11);
        if (found < 0 || found > 102) return null;
        values.push(found);
        index += 6;
        if (values.length > 90) return null;
    }
    return null;
};

/* Ширины полос одной линии: за каждым тёмным элементом пробуем все форматы — в прямом и обратном
   направлении (наклейку держат и вверх ногами). runs[0] — всегда светлый (поле перед кодом). */
export const decodeRuns = (runs) => {
    if (runs.length < 20) return null;
    // Обратный проход тоже должен начинаться светлой полосой: если линия кончилась тёмной — добавим пустую.
    const reversed = [...runs].reverse();
    if (runs.length % 2 === 0) reversed.unshift(0);
    for (const line of [runs, reversed]) {
        for (let at = 1; at < line.length; at += 2) {
            const found = decodeEan13(line, at) || decodeCode128(line, at) || decodeEan8(line, at);
            if (found) return found;
        }
    }
    return null;
};

/* ── От яркости к ширинам ───────────────────────────────────────────────── */
/* Местный порог: середина между самым тёмным и самым светлым в окне. Где перепада почти нет —
   светлое поле. Граница полосы — точка, где сглаженная яркость пересекает порог (с долей пикселя). */
export const runsFromSamples = (samples) => {
    const n = samples.length;
    if (n < 30) return [];
    // Сглаживание — слабое (¼·½·¼ даёт тонкой полосе в 2 пикселя потерять контраст): шум гасим
    // наполовину, а границы считаем по нему же — так ширины узких полос не «расползаются».
    const smooth = new Float32Array(n);
    for (let index = 0; index < n; index += 1) {
        const a = samples[Math.max(0, index - 1)];
        const b = samples[index];
        const c = samples[Math.min(n - 1, index + 1)];
        smooth[index] = (a + 6 * b + c) / 8;
    }
    const half = Math.max(8, Math.round(n / 30));
    const threshold = new Float32Array(n);
    const flat = new Uint8Array(n);
    // Окно min/max — «скользящим блоком»: на линии в тысячу точек это быстро и без очереди.
    for (let index = 0; index < n; index += 1) {
        let low = 255;
        let high = 0;
        const from = Math.max(0, index - half);
        const to = Math.min(n - 1, index + half);
        for (let k = from; k <= to; k += 2) {
            const value = smooth[k];
            if (value < low) low = value;
            if (value > high) high = value;
        }
        threshold[index] = (low + high) / 2;
        flat[index] = high - low < 28 ? 1 : 0;
    }
    const dark = (index) => !flat[index] && smooth[index] < threshold[index];
    const edges = [];
    let previous = dark(0);
    for (let index = 1; index < n; index += 1) {
        const current = dark(index);
        if (current !== previous) {
            const a = smooth[index - 1] - threshold[index - 1];
            const b = smooth[index] - threshold[index];
            const t = a === b ? 0.5 : Math.min(1, Math.max(0, a / (a - b)));
            edges.push({ at: index - 1 + t, dark: current });
            previous = current;
        }
    }
    if (!edges.length) return [];
    // Первая полоса — светлая (от начала линии до первого тёмного).
    const runs = [];
    let from = 0;
    let startDark = dark(0);
    if (startDark) { runs.push(0); }
    for (const edge of edges) {
        runs.push(edge.at - from);
        from = edge.at;
    }
    runs.push(n - from);
    return runs;
};

/* Второй способ — по перепадам, а не по порогу. На размытом снимке узкий светлый зазор между двумя
   толстыми штрихами не доходит до порога яркости, и полосы сливаются (наклейка с фото владельца:
   из 59 полос порог находил 57). Перепад же у такого зазора есть всегда: подъём и спад. Граница —
   пик производной (с долей пикселя по параболе); пики чередуются «в тёмное — в светлое», лишний
   того же знака отдаёт место более сильному. */
export const runsFromEdges = (samples) => {
    const n = samples.length;
    if (n < 30) return [];
    const smooth = new Float32Array(n);
    for (let index = 0; index < n; index += 1) {
        smooth[index] = (samples[Math.max(0, index - 1)] + 2 * samples[index] + samples[Math.min(n - 1, index + 1)]) / 4;
    }
    const grad = new Float32Array(n);
    for (let index = 1; index < n - 1; index += 1) grad[index] = (smooth[index + 1] - smooth[index - 1]) / 2;
    const half = Math.max(8, Math.round(n / 30));
    const edges = [];
    for (let index = 2; index < n - 2; index += 1) {
        const value = Math.abs(grad[index]);
        if (value < Math.abs(grad[index - 1]) || value <= Math.abs(grad[index + 1])) continue;
        let low = 255;
        let high = 0;
        for (let k = Math.max(0, index - half); k <= Math.min(n - 1, index + half); k += 2) {
            if (smooth[k] < low) low = smooth[k];
            if (smooth[k] > high) high = smooth[k];
        }
        if (high - low < 28 || value < Math.max(4, (high - low) * 0.07)) continue;
        const a = Math.abs(grad[index - 1]);
        const c = Math.abs(grad[index + 1]);
        const bend = a - 2 * value + c;
        const at = index + (bend < 0 ? (a - c) / (2 * bend) : 0);
        const edge = { at, toDark: grad[index] < 0, power: value };
        const last = edges[edges.length - 1];
        if (last && last.toDark === edge.toDark) {
            if (edge.power > last.power) edges[edges.length - 1] = edge;
        } else {
            edges.push(edge);
        }
    }
    if (!edges.length) return [];
    const runs = [];
    if (!edges[0].toDark) runs.push(0);
    let from = 0;
    for (const edge of edges) {
        runs.push(edge.at - from);
        from = edge.at;
    }
    runs.push(n - from);
    return runs;
};

/* Яркость вдоль отрезка (x0,y0)→(x1,y1) кадра в оттенках серого. */
const sampleLine = (gray, width, height, x0, y0, x1, y1) => {
    const length = Math.max(2, Math.round(Math.hypot(x1 - x0, y1 - y0)));
    const out = new Float32Array(length);
    for (let index = 0; index < length; index += 1) {
        const t = index / (length - 1);
        const x = Math.min(width - 1, Math.max(0, Math.round(x0 + (x1 - x0) * t)));
        const y = Math.min(height - 1, Math.max(0, Math.round(y0 + (y1 - y0) * t)));
        out[index] = gray[y * width + x];
    }
    return out;
};

/* Нерезкая маска строки: s + amount·(s − среднее по окну ±radius). Размытая камера (веб-камера ноутбука
   с постоянным фокусом, наклейка вблизи) сливает узкие полосы, и ни порог, ни перепады их не видят —
   резкость их возвращает. Модуль на снимке заранее неизвестен, поэтому радиус перебирается
   (SHARPEN_PASSES; на синтетике с сильным размытием 23 → 77 прочтений из 80, ложных — ноль). */
export const SHARPEN_PASSES = [[2, 4], [3, 4], [1, 2], [4, 2]];

export const sharpenSamples = (samples, radius, amount) => {
    const n = samples.length;
    const prefix = new Float64Array(n + 1);
    for (let index = 0; index < n; index += 1) prefix[index + 1] = prefix[index] + samples[index];
    const out = new Float32Array(n);
    for (let index = 0; index < n; index += 1) {
        const from = Math.max(0, index - radius);
        const to = Math.min(n - 1, index + radius);
        const mean = (prefix[to + 1] - prefix[from]) / (to - from + 1);
        const value = samples[index] + amount * (samples[index] - mean);
        out[index] = value < 0 ? 0 : (value > 255 ? 255 : value);
    }
    return out;
};

/* Одна строка → код: порог, перепады, затем те же два способа на строке с поднятой резкостью. */
const decodeLine = (samples, sharpen) => {
    const found = decodeRuns(runsFromSamples(samples)) || decodeRuns(runsFromEdges(samples));
    if (found) return found;
    for (const [radius, amount] of sharpen) {
        const sharp = sharpenSamples(samples, radius, amount);
        const again = decodeRuns(runsFromSamples(sharp)) || decodeRuns(runsFromEdges(sharp));
        if (again) return again;
    }
    return null;
};

/* Кадр (серый, width×height) → прочитанный код или null. Линии — через середину кадра (туда целятся
   рамкой), горизонтально и под наклоном ±8° и ±16°. confirm — сколько линий должны прочитать одно и то
   же: у EAN и Code 128 есть контрольная цифра, но два согласных прочтения — защита от случайного мусора.
   all — не первый код, а все подтверждённые, чаще прочитанные первыми (крупная наклейка пересекает
   больше линий, чем мелкий заводской штрихкод рядом). sharpen — какие проходы резкости пробовать
   на строке, которую так не прочитать (живой кадр берёт по одному, чередуя от кадра к кадру). */
export const scanGray = (gray, width, height, {
    lines = 13, angles = [0, 8, -8, 16, -16], confirm = 2, band = [0.2, 0.8], all = false, withHits = false,
    sharpen = SHARPEN_PASSES,
} = {}) => {
    const counts = new Map();
    const cx = width / 2;
    for (const degrees of angles) {
        const radians = (degrees * Math.PI) / 180;
        const dx = Math.cos(radians);
        const dy = Math.sin(radians);
        const half = width * 0.48;
        for (let line = 0; line < lines; line += 1) {
            const cy = height * (band[0] + ((band[1] - band[0]) * line) / Math.max(1, lines - 1));
            const samples = sampleLine(gray, width, height, cx - dx * half, cy - dy * half, cx + dx * half, cy + dy * half);
            const found = decodeLine(samples, sharpen);
            if (!found) continue;
            const key = `${found.format}:${found.text}`;
            const seen = (counts.get(key)?.hits || 0) + 1;
            counts.set(key, { ...found, hits: seen });
            if (seen >= confirm && !all) return found;
        }
    }
    if (!all) return null;
    const confirmed = [...counts.values()].filter((item) => item.hits >= confirm).sort((a, b) => b.hits - a.hits);
    return withHits ? confirmed : confirmed.map(({ format, text }) => ({ format, text }));
};

/* Снимок целиком (фото наклейки из файла) → все коды на нём, чаще прочитанные первыми: рядом с
   наклейкой бывает заводской штрихкод, и какой из них в учёте, решает поиск. Штрихкод может быть где
   угодно — строк больше и по всей высоте — и как угодно повёрнут: снимок читается и прямо, и повёрнутым
   на 90°, всегда оба раза (заводская этикетка стоит прямо, а наклейка бухгалтерии бывает боком). */
export const scanStill = (gray, width, height) => {
    const options = (rows, sharpen) => ({
        lines: Math.max(30, Math.min(220, Math.round(rows / 6))), band: [0.03, 0.97], angles: [0, 6, -6, 12, -12, 20, -20],
        all: true, withHits: true, sharpen,
    });
    const turned = new Uint8Array(width * height);
    for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) turned[x * height + (height - 1 - y)] = gray[y * width + x];
    }
    // Сначала без резкости — чёткое фото читается за доли секунды; ничего — те же строки с проходами
    // резкости (размытый снимок): каждая пустая строка проходит их все, это в разы дольше.
    for (const sharpen of [[], SHARPEN_PASSES]) {
        const merged = new Map();
        [...scanGray(gray, width, height, options(height, sharpen)), ...scanGray(turned, height, width, options(width, sharpen))]
            .forEach((item) => {
                const key = `${item.format}:${item.text}`;
                if ((merged.get(key)?.hits || 0) < item.hits) merged.set(key, item);
            });
        if (merged.size) return [...merged.values()].sort((a, b) => b.hits - a.hits).map(({ format, text }) => ({ format, text }));
    }
    return [];
};

/* RGBA кадра → серый (Uint8Array). */
export const toGray = (rgba, width, height) => {
    const gray = new Uint8Array(width * height);
    for (let index = 0, pixel = 0; index < gray.length; index += 1, pixel += 4) {
        gray[index] = (rgba[pixel] * 77 + rgba[pixel + 1] * 150 + rgba[pixel + 2] * 29) >> 8;
    }
    return gray;
};
