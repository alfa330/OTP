/*
 * Скан документа камерой — раздел «Дисциплина» (владелец, 23.10.2026: «при
 * скане открывается камера и делается скан документа на фото»).
 *
 * Здесь вся математика скана, без DOM: найти лист на кадре, выпрямить его по
 * четырём углам, сделать «как скан» (белый фон без теней, чернила своего
 * цвета) и собрать страницы в PDF. Окно камеры, рамка и углы — в
 * DocumentScanner.jsx. Функции работают с массивами пикселей, поэтому их
 * проверяет node-тест на синтетических кадрах (tests/discipline_doc_scan.test.mjs).
 *
 * Почему своё, а не OpenCV.js: тот весит ~8 МБ, а оператор открывает раздел с
 * телефона по мобильной сети. Поиск листа здесь — классический: границы
 * (Канни) → прямые (Хаф, голос только вдоль градиента) → лучший
 * четырёхугольник из двух пар почти параллельных прямых, по доле границы,
 * которая действительно лежит на его сторонах. Ошибку поиска человек поправит
 * сам: углы рамки после снимка перетаскиваются.
 */

/* ── Что умеет скан и куда он годится ─────────────────────────────────── */

export const SCAN_FILTERS = [
    { value: 'color', label: 'Цвет' },
    { value: 'gray', label: 'Ч/б' },
    { value: 'photo', label: 'Фото' },
];

export const SCAN_FILTER_HINT = 'Цвет — белый лист без теней, чернила своего цвета: подпись синей ручкой '
    + 'остаётся синей. Ч/б — как ксерокопия. Фото — снимок без обработки, только выпрямлен по краям листа.';

export const SCAN_DEFAULT_FILTER = 'color';

/* Страниц в одном скане. Одна страница уходит фото (JPEG), несколько — одним
   PDF: у объяснительной и акта сервер принимает один файл. */
export const SCAN_MAX_PAGES = 10;

/* Длинная сторона страницы в готовом файле: ~200 точек на дюйм для листа A4 —
   рукопись и подпись читаются, файл не раздувается. */
export const SCAN_MAX_SIDE = 2400;

/* Потолок всего файла — с запасом до самого строгого предела вида файла, куда
   кнопка предлагает скан (10 МБ у объяснительной и доказательства). */
export const SCAN_TARGET_BYTES = 9 * 1024 * 1024;

const ACCEPT_JPEG = ['.jpg', '.jpeg', 'image/jpeg', 'image/*'];
const ACCEPT_PDF = ['.pdf', 'application/pdf'];

/* Во что можно сохранить скан для поля с таким accept. Скан предлагается,
   только если сервер примет фото или PDF; несколько страниц — только в PDF. */
export const scanFormats = (accept) => {
    const list = String(accept || '').toLowerCase().split(',').map((item) => item.trim()).filter(Boolean);
    const jpeg = list.some((item) => ACCEPT_JPEG.includes(item));
    const pdf = list.some((item) => ACCEPT_PDF.includes(item));
    return { jpeg, pdf, any: jpeg || pdf };
};

const pad2 = (value) => String(value).padStart(2, '0');

/* «Скан 23.10.2026 17-40.pdf» — двоеточие сервер из имени вырезает. */
export const scanFileName = (date, ext) => {
    const d = date instanceof Date && !Number.isNaN(date.getTime()) ? date : new Date();
    return `Скан ${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()} ${pad2(d.getHours())}-${pad2(d.getMinutes())}.${ext}`;
};

/* ── Геометрия ────────────────────────────────────────────────────────── */

const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);

/* Углы по порядку: левый верхний, правый верхний, правый нижний, левый нижний. */
export const orderCorners = (points) => {
    const cx = points.reduce((sum, p) => sum + p[0], 0) / points.length;
    const cy = points.reduce((sum, p) => sum + p[1], 0) / points.length;
    const sorted = [...points].sort((a, b) => Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx));
    let start = 0;
    sorted.forEach((p, index) => {
        if (p[0] + p[1] < sorted[start][0] + sorted[start][1]) start = index;
    });
    return [...sorted.slice(start), ...sorted.slice(0, start)].map((p) => [p[0], p[1]]);
};

/* Четырёхугольник годится для выпрямления: выпуклый и без вырожденных углов. */
export const isConvexQuad = (corners) => {
    if (!Array.isArray(corners) || corners.length !== 4) return false;
    let sign = 0;
    for (let i = 0; i < 4; i += 1) {
        const a = corners[i];
        const b = corners[(i + 1) % 4];
        const c = corners[(i + 2) % 4];
        const cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
        if (Math.abs(cross) < 1e-6) return false;
        const s = Math.sign(cross);
        if (sign && s !== sign) return false;
        sign = s;
    }
    return true;
};

export const quadArea = (corners) => {
    let sum = 0;
    for (let i = 0; i < corners.length; i += 1) {
        const a = corners[i];
        const b = corners[(i + 1) % corners.length];
        sum += a[0] * b[1] - b[0] * a[1];
    }
    return Math.abs(sum) / 2;
};

/* Рамка по умолчанию, когда лист не нашёлся: кадр с полями. */
export const defaultCorners = (w, h, inset = 0.06) => [
    [w * inset, h * inset], [w * (1 - inset), h * inset], [w * (1 - inset), h * (1 - inset)], [w * inset, h * (1 - inset)],
];

export const scaleCorners = (corners, sx, sy = sx) => corners.map((p) => [p[0] * sx, p[1] * sy]);

/* Решение 8×8 методом Гаусса с выбором главного элемента. */
const solveLinear = (matrix, rhs) => {
    const n = rhs.length;
    const a = matrix.map((row, i) => [...row, rhs[i]]);
    for (let col = 0; col < n; col += 1) {
        let pivot = col;
        for (let row = col + 1; row < n; row += 1) {
            if (Math.abs(a[row][col]) > Math.abs(a[pivot][col])) pivot = row;
        }
        if (Math.abs(a[pivot][col]) < 1e-12) return null;
        [a[col], a[pivot]] = [a[pivot], a[col]];
        for (let row = 0; row < n; row += 1) {
            if (row === col) continue;
            const factor = a[row][col] / a[col][col];
            if (!factor) continue;
            for (let k = col; k <= n; k += 1) a[row][k] -= factor * a[col][k];
        }
    }
    return a.map((row, i) => row[n] / row[i]);
};

/* Проективное преобразование, переводящее четыре точки from в четыре точки to. */
export const homography = (from, to) => {
    const matrix = [];
    const rhs = [];
    for (let i = 0; i < 4; i += 1) {
        const [x, y] = from[i];
        const [u, v] = to[i];
        matrix.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
        rhs.push(u);
        matrix.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
        rhs.push(v);
    }
    const h = solveLinear(matrix, rhs);
    return h ? [...h, 1] : null;
};

export const applyHomography = (H, x, y) => {
    const d = H[6] * x + H[7] * y + H[8];
    return [(H[0] * x + H[1] * y + H[2]) / d, (H[3] * x + H[4] * y + H[5]) / d];
};

const cross3 = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/* Соотношение сторон листа (ширина / высота) по его снимку в перспективе —
   метод Чжана и Хэ (2003) с ТИПИЧНЫМ фокусом камеры, а не оценённым по углам.

   Почему не оценённый. Сам метод умеет вывести фокус из четырёх углов, но при
   слабой перспективе (лист снят почти прямо — обычный случай) эта оценка плохо
   обусловлена: шум углов в 0,3 % кадра (4 px на кадре 1280) давал медиану
   ошибки пропорций 1 %, а хвост — до 164 %, то есть лист вдвое уже настоящего.
   Так и вышло на стенде (23.10.2026). У камер телефонов и ноутбуков фокус почти
   одинаковый: основная камера телефона (26 мм в эквиваленте) и обычная
   веб-камера — 0,72 длинной стороны кадра. С ним на 4000 синтетических снимках
   с настоящим фокусом от 0,6 до 1,0 медиана ошибки 0,9 %, у 99 % — не больше
   4,2 %, худший — 6,2 %; в окно приведения к A4 (5 %) попадают 99,8 % листов.
   Наивные пропорции по длинам сторон — до 19 %. Центр кадра — главная точка. */
const TYPICAL_FOCAL = 0.72;

export const documentAspect = (corners, w, h) => {
    const [tl, tr, br, bl] = corners;
    const u0 = w / 2;
    const v0 = h / 2;
    const m1 = [tl[0] - u0, tl[1] - v0, 1];
    const m2 = [tr[0] - u0, tr[1] - v0, 1];
    const m3 = [bl[0] - u0, bl[1] - v0, 1];
    const m4 = [br[0] - u0, br[1] - v0, 1];
    const d2 = dot3(cross3(m2, m4), m3);
    const d3 = dot3(cross3(m3, m4), m2);
    if (!d2 || !d3) return null;
    const k2 = dot3(cross3(m1, m4), m3) / d2;
    const k3 = dot3(cross3(m1, m4), m2) / d3;
    const n2 = [k2 * m2[0] - m1[0], k2 * m2[1] - m1[1], k2 * m2[2] - m1[2]];
    const n3 = [k3 * m3[0] - m1[0], k3 * m3[1] - m1[1], k3 * m3[2] - m1[2]];
    const f2 = (TYPICAL_FOCAL * Math.max(w, h)) ** 2;
    const ratio = Math.sqrt((n2[0] ** 2 + n2[1] ** 2 + f2 * n2[2] ** 2) / (n3[0] ** 2 + n3[1] ** 2 + f2 * n3[2] ** 2));
    return Number.isFinite(ratio) && ratio > 0.15 && ratio < 7 ? ratio : null;
};

const A4 = Math.SQRT2;
const A4_SNAP = 0.05;

/* Размер выпрямленной страницы в пикселях. Ни одна сторона не теряет
   разрешения снимка; пропорции — настоящие (documentAspect), а близкие к A4
   приводятся к A4 ровно: почти все объяснительные и акты — на листе A4. */
export const outputSize = (corners, srcW, srcH, maxSide = SCAN_MAX_SIDE) => {
    const [tl, tr, br, bl] = corners;
    const widthPx = Math.max(dist(tl, tr), dist(bl, br), 1);
    const heightPx = Math.max(dist(tl, bl), dist(tr, br), 1);
    let aspect = documentAspect(corners, srcW, srcH) || widthPx / heightPx;
    if (Math.abs(aspect * A4 - 1) < A4_SNAP) aspect = 1 / A4;
    else if (Math.abs(aspect / A4 - 1) < A4_SNAP) aspect = A4;
    let width = Math.max(widthPx, heightPx * aspect);
    let height = width / aspect;
    const scale = Math.min(1, maxSide / Math.max(width, height));
    width = Math.max(1, Math.round(width * scale));
    height = Math.max(1, Math.round(height * scale));
    return { width, height };
};

/* Поля, срезаемые с краёв выпрямленной страницы, — доля длинной стороны.
   Рамка листа стоит на середине размытой кромки, и полоска стола в 2–3 px
   попадала в страницу: «как скан» вытягивал её в тёмный пунктир по краю
   (стенд, 23.10.2026). Поля листа пустые: срез в 1,2 % — 3,6 мм у листа A4. */
export const SCAN_EDGE_TRIM = 0.012;

/* Углы того же листа, отступившие внутрь на долю trim от длинной стороны
   страницы width×height. Отступ ставится на выпрямленной странице и
   переносится на снимок той же проекцией: у листа в перспективе ближний край
   срезается шире дальнего — как и должно. */
export const insetCorners = (corners, width, height, trim = SCAN_EDGE_TRIM) => {
    const H = homography([[0, 0], [width, 0], [width, height], [0, height]], corners);
    if (!H) return corners;
    const m = Math.max(width, height) * trim;
    return [[m, m], [width - m, m], [width - m, height - m], [m, height - m]].map(([x, y]) => applyHomography(H, x, y));
};

/* Выпрямить четырёхугольник corners снимка src (RGBA, sw×sh) в прямоугольник
   ow×oh. Билинейная выборка; то, что выходит за край снимка, — белое (лист). */
export const warpPerspective = (src, sw, sh, corners, ow, oh) => {
    const H = homography([[0, 0], [ow, 0], [ow, oh], [0, oh]], corners);
    const out = new Uint8ClampedArray(ow * oh * 4);
    if (!H) return out.fill(255);
    const maxX = sw - 1;
    const maxY = sh - 1;
    let o = 0;
    for (let v = 0; v < oh; v += 1) {
        const vy = v + 0.5;
        let nx = H[0] * 0.5 + H[1] * vy + H[2];
        let ny = H[3] * 0.5 + H[4] * vy + H[5];
        let dd = H[6] * 0.5 + H[7] * vy + H[8];
        for (let u = 0; u < ow; u += 1, o += 4) {
            const x = nx / dd - 0.5;
            const y = ny / dd - 0.5;
            nx += H[0];
            ny += H[3];
            dd += H[6];
            if (x < -1 || y < -1 || x > sw || y > sh) {
                out[o] = 255; out[o + 1] = 255; out[o + 2] = 255; out[o + 3] = 255;
                continue;
            }
            let x0 = Math.floor(x);
            let y0 = Math.floor(y);
            const fx = x - x0;
            const fy = y - y0;
            let x1 = x0 + 1;
            let y1 = y0 + 1;
            if (x0 < 0) x0 = 0; else if (x0 > maxX) x0 = maxX;
            if (x1 < 0) x1 = 0; else if (x1 > maxX) x1 = maxX;
            if (y0 < 0) y0 = 0; else if (y0 > maxY) y0 = maxY;
            if (y1 < 0) y1 = 0; else if (y1 > maxY) y1 = maxY;
            const i00 = (y0 * sw + x0) * 4;
            const i10 = (y0 * sw + x1) * 4;
            const i01 = (y1 * sw + x0) * 4;
            const i11 = (y1 * sw + x1) * 4;
            const w00 = (1 - fx) * (1 - fy);
            const w10 = fx * (1 - fy);
            const w01 = (1 - fx) * fy;
            const w11 = fx * fy;
            out[o] = src[i00] * w00 + src[i10] * w10 + src[i01] * w01 + src[i11] * w11;
            out[o + 1] = src[i00 + 1] * w00 + src[i10 + 1] * w10 + src[i01 + 1] * w01 + src[i11 + 1] * w11;
            out[o + 2] = src[i00 + 2] * w00 + src[i10 + 2] * w10 + src[i01 + 2] * w01 + src[i11 + 2] * w11;
            out[o + 3] = 255;
        }
    }
    return out;
};

/* ── Поиск листа на кадре ─────────────────────────────────────────────── */

/* Сторона кадра, на котором ищется лист: живой поиск идёт несколько раз в
   секунду на телефоне, и больше пикселей точности почти не добавляют —
   углы человек поправит на снимке. */
export const DETECT_SIDE = 320;

export const toGray = (rgba, w, h) => {
    const out = new Uint8Array(w * h);
    for (let i = 0, j = 0; i < out.length; i += 1, j += 4) {
        out[i] = (rgba[j] * 77 + rgba[j + 1] * 150 + rgba[j + 2] * 29) >> 8;
    }
    return out;
};

/* Размытие 5×5 (биномиальное ядро 1-4-6-4-1, по строкам и столбцам). */
const blur5 = (src, w, h) => {
    const tmp = new Uint16Array(w * h);
    for (let y = 0; y < h; y += 1) {
        const row = y * w;
        for (let x = 0; x < w; x += 1) {
            const x0 = x > 1 ? x - 2 : 0;
            const x1 = x > 0 ? x - 1 : 0;
            const x3 = x < w - 1 ? x + 1 : w - 1;
            const x4 = x < w - 2 ? x + 2 : w - 1;
            tmp[row + x] = src[row + x0] + 4 * src[row + x1] + 6 * src[row + x] + 4 * src[row + x3] + src[row + x4];
        }
    }
    const out = new Uint8Array(w * h);
    for (let y = 0; y < h; y += 1) {
        const r0 = (y > 1 ? y - 2 : 0) * w;
        const r1 = (y > 0 ? y - 1 : 0) * w;
        const r2 = y * w;
        const r3 = (y < h - 1 ? y + 1 : h - 1) * w;
        const r4 = (y < h - 2 ? y + 2 : h - 1) * w;
        for (let x = 0; x < w; x += 1) {
            out[r2 + x] = (tmp[r0 + x] + 4 * tmp[r1 + x] + 6 * tmp[r2 + x] + 4 * tmp[r3 + x] + tmp[r4 + x] + 128) >> 8;
        }
    }
    return out;
};

const sobel = (p, w, h) => {
    const n = w * h;
    const gx = new Float32Array(n);
    const gy = new Float32Array(n);
    const mag = new Float32Array(n);
    for (let y = 1; y < h - 1; y += 1) {
        for (let x = 1; x < w - 1; x += 1) {
            const i = y * w + x;
            const a = p[i - w - 1];
            const b = p[i - w];
            const c = p[i - w + 1];
            const d = p[i - 1];
            const f = p[i + 1];
            const g = p[i + w - 1];
            const k = p[i + w];
            const m = p[i + w + 1];
            const sx = (c + 2 * f + m) - (a + 2 * d + g);
            const sy = (g + 2 * k + m) - (a + 2 * b + c);
            gx[i] = sx;
            gy[i] = sy;
            mag[i] = Math.sqrt(sx * sx + sy * sy);
        }
    }
    return { gx, gy, mag };
};

const HIST_BINS = 1500;
/* Ширина края кадра, где границ листа не ищем. */
const BORDER = 2;

/* Границы Канни: тонкие линии (подавление немаксимумов) и два порога с
   гистерезисом. Пороги — от распределения самих границ кадра (у листа на
   тёмном столе и у листа на светлом они очень разные), но верхний — не выше
   HIGH_CAP: штрихи текста контрастнее кромки листа, и порог «по большинству»
   на исписанном листе уходил выше кромки — лист на светлом столе пропадал. */
const HIGH_CAP = 90;
const cannyEdges = (mag, gx, gy, w, h) => {
    const n = w * h;
    const thin = new Float32Array(n);
    const hist = new Uint32Array(HIST_BINS);
    let count = 0;
    // Крайние пиксели кадра — не граница листа: у края нет соседа для
    // подавления немаксимумов, и там рождались ложные «кромки» во всю сторону.
    for (let y = BORDER; y < h - BORDER; y += 1) {
        for (let x = BORDER; x < w - BORDER; x += 1) {
            const i = y * w + x;
            const m = mag[i];
            if (m < 8) continue;
            const ax = Math.abs(gx[i]);
            const ay = Math.abs(gy[i]);
            let m1;
            let m2;
            if (ay <= ax * 0.4142) { m1 = mag[i - 1]; m2 = mag[i + 1]; }
            else if (ay >= ax * 2.4142) { m1 = mag[i - w]; m2 = mag[i + w]; }
            else if (gx[i] * gy[i] > 0) { m1 = mag[i - w - 1]; m2 = mag[i + w + 1]; }
            else { m1 = mag[i - w + 1]; m2 = mag[i + w - 1]; }
            if (m >= m1 && m > m2) {
                thin[i] = m;
                hist[Math.min(HIST_BINS - 1, m | 0)] += 1;
                count += 1;
            }
        }
    }
    const edges = new Uint8Array(n);
    if (!count) return edges;
    let seen = 0;
    let high = HIST_BINS - 1;
    for (let b = 0; b < HIST_BINS; b += 1) {
        seen += hist[b];
        if (seen >= count * 0.5) { high = b; break; }
    }
    high = Math.min(HIGH_CAP, Math.max(28, high));
    const low = Math.max(14, high * 0.45);
    const stack = new Int32Array(n);
    for (let i = 0; i < n; i += 1) {
        if (thin[i] < high || edges[i]) continue;
        edges[i] = 1;
        let top = 0;
        stack[top++] = i;
        while (top) {
            const j = stack[--top];
            const y = (j / w) | 0;
            const x = j - y * w;
            for (let dy = -1; dy <= 1; dy += 1) {
                const yy = y + dy;
                if (yy < 1 || yy >= h - 1) continue;
                for (let dx = -1; dx <= 1; dx += 1) {
                    const xx = x + dx;
                    if (xx < 1 || xx >= w - 1) continue;
                    const k = yy * w + xx;
                    if (!edges[k] && thin[k] >= low) {
                        edges[k] = 1;
                        stack[top++] = k;
                    }
                }
            }
        }
    }
    return edges;
};

/* Граница «рядом»: к точке на стороне четырёхугольника засчитывается граница
   в соседнем пикселе — прямая Хафа и настоящая кромка листа расходятся на 1 px. */
const dilate = (edges, w, h) => {
    const out = new Uint8Array(w * h);
    for (let y = 1; y < h - 1; y += 1) {
        for (let x = 1; x < w - 1; x += 1) {
            const i = y * w + x;
            if (edges[i] || edges[i - 1] || edges[i + 1] || edges[i - w] || edges[i + w]
                || edges[i - w - 1] || edges[i - w + 1] || edges[i + w - 1] || edges[i + w + 1]) out[i] = 1;
        }
    }
    return out;
};

const THETA_BINS = 180;
const COS = new Float64Array(THETA_BINS);
const SIN = new Float64Array(THETA_BINS);
for (let t = 0; t < THETA_BINS; t += 1) {
    COS[t] = Math.cos((t * Math.PI) / THETA_BINS);
    SIN[t] = Math.sin((t * Math.PI) / THETA_BINS);
}
const VOTE_SPREAD = 6;
const MAX_LINES = 32;
const PARALLEL_GAP = 40;
const MIN_AREA = 0.05;

const angleGap = (a, b) => {
    const d = Math.abs(a - b);
    return d > 90 ? 180 - d : d;
};

/* Одна и та же прямая в двух записях: на всём протяжении кадра они не
   расходятся дальше tolerance. Сравнивать только угол и расстояние до центра
   нельзя: кромка листа и соседняя полоса древесины в 3° и 4 px от неё в центре
   считались одной прямой, и кромка, набрав меньше голосов, выбрасывалась
   (стенд, 23.10.2026). Под разными углами прямые к краям кадра расходятся. */
const sameLine = (a, b, tolerance, reach) => {
    if (angleGap(a.t, b.t) > 10) return false;
    const px = a.rho * COS[a.t];
    const py = a.rho * SIN[a.t];
    const dx = -SIN[a.t];
    const dy = COS[a.t];
    for (const s of [-reach, reach]) {
        const x = px + dx * s;
        const y = py + dy * s;
        if (Math.abs(x * COS[b.t] + y * SIN[b.t] - b.rho) >= tolerance) return false;
    }
    return true;
};

/* Прямые Хафа в нормальной форме (угол нормали t в градусах, расстояние rho
   от центра кадра). Каждая точка границы голосует только за углы около своего
   градиента: так быстрее в 13 раз и пики чище — штрихи текста под другими
   углами не размазывают прямые кромки листа.

   Голос весит по силе границы (до VOTE_FULL — полный). Иначе полоса на столе
   во всю ширину кадра набирала больше, чем кромка листа втрое короче, хотя та
   втрое контрастнее: на деревянном столе 32 места списка занимали волокна, и
   сторона листа в него не попадала (стенд, 23.10.2026). */
const VOTE_FULL = 160;
const houghLines = (edges, gx, gy, mag, w, h) => {
    const cx = w / 2;
    const cy = h / 2;
    const R = Math.ceil(Math.hypot(cx, cy)) + 1;
    const width = 2 * R + 1;
    const acc = new Float32Array(THETA_BINS * width);
    for (let y = 1; y < h - 1; y += 1) {
        for (let x = 1; x < w - 1; x += 1) {
            const i = y * w + x;
            if (!edges[i]) continue;
            let t0 = Math.round((Math.atan2(gy[i], gx[i]) * 180) / Math.PI);
            t0 = ((t0 % 180) + 180) % 180;
            const px = x - cx;
            const py = y - cy;
            const vote = Math.min(1, mag[i] / VOTE_FULL);
            for (let d = -VOTE_SPREAD; d <= VOTE_SPREAD; d += 1) {
                let t = t0 + d;
                if (t < 0) t += 180;
                else if (t >= 180) t -= 180;
                const rho = Math.round(px * COS[t] + py * SIN[t]);
                acc[t * width + rho + R] += vote;
            }
        }
    }
    const minVotes = Math.max(8, Math.min(w, h) * 0.08);
    const candidates = [];
    for (let t = 0; t < THETA_BINS; t += 1) {
        const base = t * width;
        for (let r = 1; r < width - 1; r += 1) {
            const v = acc[base + r];
            if (v < minVotes) continue;
            let peak = true;
            for (let dt = -1; dt <= 1 && peak; dt += 1) {
                let tt = t + dt;
                let mirror = false;
                if (tt < 0) { tt = THETA_BINS - 1; mirror = true; }
                else if (tt >= THETA_BINS) { tt = 0; mirror = true; }
                for (let dr = -1; dr <= 1; dr += 1) {
                    if (!dt && !dr) continue;
                    const rr = mirror ? width - 1 - (r + dr) : r + dr;
                    if (rr < 0 || rr >= width) continue;
                    const u = acc[tt * width + rr];
                    if (u > v || (u === v && (dt < 0 || (dt === 0 && dr < 0)))) { peak = false; break; }
                }
            }
            if (peak) candidates.push({ t, rho: r - R, votes: v });
        }
    }
    candidates.sort((a, b) => b.votes - a.votes);
    const tolerance = Math.max(4, Math.max(w, h) * 0.02);
    const reach = Math.hypot(cx, cy);
    const lines = [];
    for (const candidate of candidates) {
        if (lines.length >= MAX_LINES) break;
        if (lines.some((line) => sameLine(line, candidate, tolerance, reach))) continue;
        lines.push(candidate);
    }
    return lines;
};

const intersect = (a, b, cx, cy) => {
    const det = COS[a.t] * SIN[b.t] - SIN[a.t] * COS[b.t];
    if (Math.abs(det) < 0.25) return null;
    const x = (a.rho * SIN[b.t] - b.rho * SIN[a.t]) / det;
    const y = (b.rho * COS[a.t] - a.rho * COS[b.t]) / det;
    return [x + cx, y + cy];
};

/* Сторона p→q кандидата: какая доля её точек лежит на границе (share), какая
   доля внутри кадра (inside) и «вес» — длина, засчитанная точкам на границе,
   где лист светлее того, что снаружи.

   Контраст — в каждой точке, а не средним по стороне: лист светлее стола
   почти всегда, и это отличает настоящую кромку от полосы на столе (волокно
   дерева, край клавиатуры) и от строки текста внутри листа — у них по обе
   стороны одно и то же. Без этого счёт выбирал рамку больше листа: стороны,
   продлённые по столу за угол листа, и сторона по полосе древесины набирали
   «длину» (стенд, 23.10.2026). Точку, где сравнить не с чем, считаем как без
   контраста. */
const contrastWeight = (contrast) => Math.min(1, Math.max(0.15, (contrast + 10) / 50));

const sideScore = (p, q, support, gray, center, w, h) => {
    const len = dist(p, q);
    const steps = Math.max(8, Math.round(len / 1.5));
    let nx = -(q[1] - p[1]);
    let ny = q[0] - p[0];
    const norm = Math.hypot(nx, ny) || 1;
    nx /= norm;
    ny /= norm;
    const mx = (p[0] + q[0]) / 2;
    const my = (p[1] + q[1]) / 2;
    if ((center[0] - mx) * nx + (center[1] - my) * ny < 0) { nx = -nx; ny = -ny; } // нормаль — внутрь
    const offset = Math.max(3, Math.min(w, h) * 0.02);
    const maxX = w - 1 - BORDER;
    const maxY = h - 1 - BORDER;
    let hits = 0;
    let valid = 0;
    let weighted = 0;
    for (let s = 0; s <= steps; s += 1) {
        const fx = p[0] + ((q[0] - p[0]) * s) / steps;
        const fy = p[1] + ((q[1] - p[1]) * s) / steps;
        const x = Math.round(fx);
        const y = Math.round(fy);
        if (x < BORDER || y < BORDER || x > maxX || y > maxY) continue;
        valid += 1;
        if (!support[y * w + x]) continue;
        hits += 1;
        const xi = Math.round(fx + nx * offset);
        const yi = Math.round(fy + ny * offset);
        // Снаружи — прижатая к краю кадра точка, если она всё ещё по ту сторону
        // кромки: у листа во весь кадр полоска стола в 3–4 px, и точка замера
        // выходила за кадр — сторона теряла вес, и выигрывала рамка по строке текста.
        const xo = Math.min(w - 1, Math.max(0, Math.round(fx - nx * offset)));
        const yo = Math.min(h - 1, Math.max(0, Math.round(fy - ny * offset)));
        const outward = (fx - xo) * nx + (fy - yo) * ny >= 1;
        const known = outward && xi >= 0 && yi >= 0 && xi < w && yi < h;
        weighted += contrastWeight(known ? gray[yi * w + xi] - gray[yo * w + xo] : 0);
    }
    return { share: valid ? hits / valid : 0, inside: valid / (steps + 1), len, weighted: (weighted * len) / steps };
};

const interiorAngleOk = (corners) => {
    for (let i = 0; i < 4; i += 1) {
        const prev = corners[(i + 3) % 4];
        const cur = corners[i];
        const next = corners[(i + 1) % 4];
        const ax = prev[0] - cur[0];
        const ay = prev[1] - cur[1];
        const bx = next[0] - cur[0];
        const by = next[1] - cur[1];
        const cos = (ax * bx + ay * by) / (Math.hypot(ax, ay) * Math.hypot(bx, by) || 1);
        if (cos > 0.82 || cos < -0.82) return false; // угол вне 35°…145°
    }
    return true;
};

/* Лист на кадре серого gray (w×h): { corners: [ЛВ, ПВ, ПН, ЛН], coverage } или
   null. coverage — средняя доля сторон, лежащая на найденной границе. */
export const detectDocument = (gray, w, h) => {
    if (!gray || w < 32 || h < 32) return null;
    const blurred = blur5(gray, w, h);
    const { gx, gy, mag } = sobel(blurred, w, h);
    const edges = cannyEdges(mag, gx, gy, w, h);
    const lines = houghLines(edges, gx, gy, mag, w, h);
    if (lines.length < 4) return null;
    const support = dilate(edges, w, h);
    const cx = w / 2;
    const cy = h / 2;
    const minSide = Math.min(w, h);
    const pairs = [];
    for (let i = 0; i < lines.length; i += 1) {
        for (let j = i + 1; j < lines.length; j += 1) {
            const a = lines[i];
            const b = lines[j];
            // Противоположные стороны в сильной перспективе расходятся до ~35°.
            if (angleGap(a.t, b.t) > PARALLEL_GAP) continue;
            const gap = Math.abs(a.t - b.t) > 90 ? Math.abs(a.rho + b.rho) : Math.abs(a.rho - b.rho);
            if (gap < minSide * 0.18) continue;
            pairs.push([a, b]);
        }
    }
    const area = w * h;
    const scored = [];
    for (let i = 0; i < pairs.length; i += 1) {
        for (let j = i + 1; j < pairs.length; j += 1) {
            const [a1, a2] = pairs[i];
            const [b1, b2] = pairs[j];
            if (angleGap(a1.t, b1.t) < 45) continue;
            const p11 = intersect(a1, b1, cx, cy);
            const p12 = intersect(a1, b2, cx, cy);
            const p22 = intersect(a2, b2, cx, cy);
            const p21 = intersect(a2, b1, cx, cy);
            if (!p11 || !p12 || !p22 || !p21) continue;
            const ring = [p11, p12, p22, p21];
            if (ring.some(([x, y]) => x < -0.08 * w || y < -0.08 * h || x > 1.08 * w || y > 1.08 * h)) continue;
            if (!isConvexQuad(ring) || quadArea(ring) < area * MIN_AREA || !interiorAngleOk(ring)) continue;
            let total = 0;
            let shares = 0;
            let ok = true;
            const center = [ring.reduce((sum, pt) => sum + pt[0], 0) / 4, ring.reduce((sum, pt) => sum + pt[1], 0) / 4];
            for (let k = 0; k < 4; k += 1) {
                // Сторона листа может уходить за край кадра (угол не влез) —
                // засчитывается только её видимая часть. Иначе такую сторону
                // отбрасывало, и рамку достраивало по строке текста внутри листа.
                const side = sideScore(ring[k], ring[(k + 1) % 4], support, blurred, center, w, h);
                if (side.len < minSide * 0.15 || side.inside < 0.3 || side.share < 0.3) { ok = false; break; }
                total += side.weighted;
                shares += side.share;
            }
            if (!ok || shares / 4 < 0.5) continue;
            scored.push({ ring, score: total, coverage: shares / 4 });
        }
    }
    if (!scored.length) return null;
    let best = scored[0];
    for (const candidate of scored) if (candidate.score > best.score) best = candidate;
    return { corners: orderCorners(best.ring), coverage: best.coverage };
};

/* Живая рамка в видоискателе: не мигает от кадра к кадру. Новое положение
   принимается после двух согласных кадров, близкое — сглаживается, рамка
   гаснет после нескольких кадров без листа. */
export const createQuadTracker = ({ hold = 3, tolerance = 0.06 } = {}) => {
    let shown = null;
    let pending = null;
    let misses = 0;
    const near = (a, b, size) => a.every((p, i) => dist(p, b[i]) < tolerance * size);
    return {
        push(quad, w, h) {
            const size = Math.hypot(w, h);
            if (!quad) {
                misses += 1;
                if (misses >= hold) { shown = null; pending = null; }
                return shown;
            }
            misses = 0;
            if (shown && near(shown, quad, size)) {
                shown = shown.map((p, i) => [p[0] + (quad[i][0] - p[0]) * 0.5, p[1] + (quad[i][1] - p[1]) * 0.5]);
                pending = null;
                return shown;
            }
            if (pending && near(pending, quad, size)) {
                shown = quad;
                pending = null;
            } else {
                pending = quad;
            }
            return shown;
        },
        reset() { shown = null; pending = null; misses = 0; },
        get current() { return shown; },
    };
};

/* ── «Как скан» ───────────────────────────────────────────────────────── */

/* Цвет бумаги по клеткам: средний цвет светлой четверти пикселей клетки —
   это бумага и в клетке, плотно исписанной текстом. Клетки, целиком занятые
   тёмным (печать, логотип), берут бумагу у соседей; потом сглаживание, чтобы
   на границах клеток не было ступенек. Деление на этот фон убирает тени и
   неровный свет, а белый баланс выходит сам: бумага становится нейтрально белой. */
const paperBackground = (data, w, h) => {
    const block = Math.max(12, Math.round(Math.max(w, h) / 40));
    const gw = Math.ceil(w / block);
    const gh = Math.ceil(h / block);
    const cells = gw * gh;
    const BINS = 64;
    const hist = new Uint32Array(cells * BINS);
    const counts = new Uint32Array(cells);
    for (let y = 0; y < h; y += 2) {
        const cellRow = ((y / block) | 0) * gw;
        for (let x = 0; x < w; x += 2) {
            const i = (y * w + x) * 4;
            const lum = (data[i] * 77 + data[i + 1] * 150 + data[i + 2] * 29) >> 8;
            const cell = cellRow + ((x / block) | 0);
            hist[cell * BINS + (lum >> 2)] += 1;
            counts[cell] += 1;
        }
    }
    const threshold = new Uint16Array(cells);
    for (let c = 0; c < cells; c += 1) {
        const need = counts[c] * 0.75;
        let seen = 0;
        let bin = 0;
        for (; bin < BINS; bin += 1) {
            seen += hist[c * BINS + bin];
            if (seen >= need) break;
        }
        threshold[c] = Math.min(bin, BINS - 1) * 4;
    }
    const sums = new Float64Array(cells * 4);
    for (let y = 0; y < h; y += 2) {
        const cellRow = ((y / block) | 0) * gw;
        for (let x = 0; x < w; x += 2) {
            const i = (y * w + x) * 4;
            const lum = (data[i] * 77 + data[i + 1] * 150 + data[i + 2] * 29) >> 8;
            const cell = cellRow + ((x / block) | 0);
            if (lum < threshold[cell]) continue;
            sums[cell * 4] += data[i];
            sums[cell * 4 + 1] += data[i + 1];
            sums[cell * 4 + 2] += data[i + 2];
            sums[cell * 4 + 3] += 1;
        }
    }
    let grid = new Float32Array(cells * 3);
    const lums = new Float32Array(cells);
    for (let c = 0; c < cells; c += 1) {
        const n = sums[c * 4 + 3] || 1;
        grid[c * 3] = sums[c * 4] / n;
        grid[c * 3 + 1] = sums[c * 4 + 1] / n;
        grid[c * 3 + 2] = sums[c * 4 + 2] / n;
        lums[c] = 0.3 * grid[c * 3] + 0.59 * grid[c * 3 + 1] + 0.11 * grid[c * 3 + 2];
    }
    // Тёмная клетка (вся в печати) — цвет самой светлой соседки.
    const lifted = new Float32Array(cells * 3);
    for (let gy = 0; gy < gh; gy += 1) {
        for (let gx = 0; gx < gw; gx += 1) {
            let pick = gy * gw + gx;
            for (let dy = -1; dy <= 1; dy += 1) {
                for (let dx = -1; dx <= 1; dx += 1) {
                    const yy = gy + dy;
                    const xx = gx + dx;
                    if (yy < 0 || xx < 0 || yy >= gh || xx >= gw) continue;
                    const k = yy * gw + xx;
                    if (lums[k] > lums[pick]) pick = k;
                }
            }
            const c = gy * gw + gx;
            const own = lums[c];
            const best = lums[pick];
            const source = own < best * 0.85 ? pick : c;
            lifted[c * 3] = grid[source * 3];
            lifted[c * 3 + 1] = grid[source * 3 + 1];
            lifted[c * 3 + 2] = grid[source * 3 + 2];
        }
    }
    grid = lifted;
    for (let pass = 0; pass < 2; pass += 1) {
        const next = new Float32Array(cells * 3);
        for (let gy = 0; gy < gh; gy += 1) {
            for (let gx = 0; gx < gw; gx += 1) {
                let r = 0;
                let g = 0;
                let b = 0;
                let n = 0;
                for (let dy = -1; dy <= 1; dy += 1) {
                    for (let dx = -1; dx <= 1; dx += 1) {
                        const yy = gy + dy;
                        const xx = gx + dx;
                        if (yy < 0 || xx < 0 || yy >= gh || xx >= gw) continue;
                        const k = (yy * gw + xx) * 3;
                        r += grid[k];
                        g += grid[k + 1];
                        b += grid[k + 2];
                        n += 1;
                    }
                }
                const c = (gy * gw + gx) * 3;
                next[c] = r / n;
                next[c + 1] = g / n;
                next[c + 2] = b / n;
            }
        }
        grid = next;
    }
    // Нижняя граница фона: иначе почти чёрная клетка «выбеливалась» бы в серое пятно.
    const sorted = Array.from(lums).sort((a, b) => a - b);
    const median = sorted[sorted.length >> 1] || 255;
    const floor = Math.max(24, median * 0.35);
    for (let k = 0; k < grid.length; k += 1) {
        if (grid[k] < floor) grid[k] = floor;
    }
    return { grid, gw, gh, block };
};

const lut = (lo, hi, gamma) => {
    const table = new Uint8ClampedArray(256);
    for (let v = 0; v < 256; v += 1) {
        const t = Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
        table[v] = Math.round(255 * t ** gamma);
    }
    return table;
};

const COLOR_LUT = lut(8, 236, 1.15);
const GRAY_LUT = lut(20, 226, 1.5);
const SATURATION = 1.2;

/* Применить фильтр скана к странице (RGBA w×h) на месте и вернуть её же.
   color — фон белый, чернила своего цвета чуть насыщеннее; gray — как
   ксерокопия; photo — без изменений. */
export const enhancePage = (data, w, h, mode) => {
    if (mode !== 'color' && mode !== 'gray') return data;
    const { grid, gw, gh, block } = paperBackground(data, w, h);
    const rowR = new Float32Array(gw);
    const rowG = new Float32Array(gw);
    const rowB = new Float32Array(gw);
    for (let y = 0; y < h; y += 1) {
        let fy = (y + 0.5) / block - 0.5;
        if (fy < 0) fy = 0;
        if (fy > gh - 1) fy = gh - 1;
        const y0 = Math.floor(fy);
        const y1 = Math.min(gh - 1, y0 + 1);
        const ty = fy - y0;
        for (let gx = 0; gx < gw; gx += 1) {
            const a = (y0 * gw + gx) * 3;
            const b = (y1 * gw + gx) * 3;
            rowR[gx] = grid[a] + (grid[b] - grid[a]) * ty;
            rowG[gx] = grid[a + 1] + (grid[b + 1] - grid[a + 1]) * ty;
            rowB[gx] = grid[a + 2] + (grid[b + 2] - grid[a + 2]) * ty;
        }
        let i = y * w * 4;
        for (let x = 0; x < w; x += 1, i += 4) {
            let fx = (x + 0.5) / block - 0.5;
            if (fx < 0) fx = 0;
            if (fx > gw - 1) fx = gw - 1;
            const x0 = Math.floor(fx);
            const x1 = Math.min(gw - 1, x0 + 1);
            const tx = fx - x0;
            const bgR = rowR[x0] + (rowR[x1] - rowR[x0]) * tx;
            const bgG = rowG[x0] + (rowG[x1] - rowG[x0]) * tx;
            const bgB = rowB[x0] + (rowB[x1] - rowB[x0]) * tx;
            let r = (data[i] * 255) / bgR;
            let g = (data[i + 1] * 255) / bgG;
            let b = (data[i + 2] * 255) / bgB;
            if (r > 255) r = 255;
            if (g > 255) g = 255;
            if (b > 255) b = 255;
            if (mode === 'gray') {
                const v = GRAY_LUT[(r * 0.299 + g * 0.587 + b * 0.114) | 0];
                data[i] = v;
                data[i + 1] = v;
                data[i + 2] = v;
            } else {
                r = COLOR_LUT[r | 0];
                g = COLOR_LUT[g | 0];
                b = COLOR_LUT[b | 0];
                const lum = r * 0.299 + g * 0.587 + b * 0.114;
                data[i] = lum + (r - lum) * SATURATION;
                data[i + 1] = lum + (g - lum) * SATURATION;
                data[i + 2] = lum + (b - lum) * SATURATION;
            }
        }
    }
    return data;
};

/* ── PDF из страниц-JPEG ──────────────────────────────────────────────── */

const encoder = new TextEncoder();
const PT_LONG = 841.89; // длинная сторона A4 в пунктах

/* Минимальный PDF 1.4: каждая страница — одна картинка JPEG во весь лист
   (DCTDecode, без перекодирования). Длинная сторона страницы — как у A4, так
   лист печатается в настоящий размер. pages: [{ jpeg: Uint8Array, width, height }]. */
export const buildPdf = (pages) => {
    const chunks = [];
    const offsets = [];
    let length = 0;
    const push = (part) => {
        const bytes = typeof part === 'string' ? encoder.encode(part) : part;
        chunks.push(bytes);
        length += bytes.length;
    };
    const object = (id, body, stream = null) => {
        offsets[id] = length;
        push(`${id} 0 obj\n${body}\n`);
        if (stream) {
            push('stream\n');
            push(stream);
            push('\nendstream\n');
        }
        push('endobj\n');
    };
    push('%PDF-1.4\n');
    push(new Uint8Array([0x25, 0xe2, 0xe3, 0xcf, 0xd3, 0x0a]));
    const kids = pages.map((_page, index) => `${3 + index * 3} 0 R`).join(' ');
    object(1, '<< /Type /Catalog /Pages 2 0 R >>');
    object(2, `<< /Type /Pages /Kids [${kids}] /Count ${pages.length} >>`);
    pages.forEach((page, index) => {
        const id = 3 + index * 3;
        const landscape = page.width >= page.height;
        const pw = landscape ? PT_LONG : (PT_LONG * page.width) / page.height;
        const ph = landscape ? (PT_LONG * page.height) / page.width : PT_LONG;
        const w = pw.toFixed(2);
        const h = ph.toFixed(2);
        const content = encoder.encode(`q ${w} 0 0 ${h} 0 0 cm /Im0 Do Q`);
        object(id, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${w} ${h}] /Resources << /XObject << /Im0 ${id + 1} 0 R >> >> /Contents ${id + 2} 0 R >>`);
        object(id + 1, `<< /Type /XObject /Subtype /Image /Width ${page.width} /Height ${page.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${page.jpeg.length} >>`, page.jpeg);
        object(id + 2, `<< /Length ${content.length} >>`, content);
    });
    const count = 3 + pages.length * 3;
    const xref = length;
    let table = `xref\n0 ${count}\n0000000000 65535 f \n`;
    for (let id = 1; id < count; id += 1) table += `${String(offsets[id]).padStart(10, '0')} 00000 n \n`;
    push(table);
    push(`trailer\n<< /Size ${count} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
    const out = new Uint8Array(length);
    let at = 0;
    for (const chunk of chunks) {
        out.set(chunk, at);
        at += chunk.length;
    }
    return out;
};
