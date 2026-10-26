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
 * телефона по мобильной сети. Поиск листа — по перепадам цвета и яркости:
 * отрезки → прямые → лучший четырёхугольник по доводам (как и почему — у
 * detectDocument). Ошибку поиска человек поправит сам: углы рамки после снимка
 * перетаскиваются.
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

/* Как снимать — как у сканера iOS. «Авто» — по умолчанию: он и есть то
   удобство, ради которого сканер, а не фото. */
export const SCAN_MODES = [
    { value: 'auto', label: 'Авто' },
    { value: 'manual', label: 'Вручную' },
];

export const SCAN_MODE_HINT = 'Авто — страница снимается сама, когда лист найден и камера не двигается; '
    + 'можно снимать и кнопкой. Вручную — снимаете кнопкой и сразу проверяете края листа.';

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

/* «Скан 23.10.2026 17-40-05.pdf» — двоеточие сервер из имени вырезает. С
   секундами: исправленную версию часто снимают в ту же минуту, и в списке
   файлов записи стояли бы два одинаковых имени. */
export const scanFileName = (date, ext) => {
    const d = date instanceof Date && !Number.isNaN(date.getTime()) ? date : new Date();
    const time = `${pad2(d.getHours())}-${pad2(d.getMinutes())}-${pad2(d.getSeconds())}`;
    return `Скан ${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()} ${time}.${ext}`;
};

/* ── Решения окна скана ───────────────────────────────────────────────── */
/* Чистые функции, чтобы их стерёг тест: в DocumentScanner.jsx они только
   вызываются. Без этого «три страницы → JPEG первой» или старая обрезка после
   «Края» проходили бы мимо всех тестов (разбор, 23.10.2026). */

/* Во что сохранить: одна страница — фото, если поле его примет; иначе и при
   нескольких страницах — один PDF. */
export const scanOutputType = (pageCount, formats) => (pageCount === 1 && formats?.jpeg ? 'jpeg' : 'pdf');

/* Ступени сжатия — по очереди, пока файл не уложится в SCAN_TARGET_BYTES. */
export const SCAN_QUALITY_STEPS = [
    { quality: 0.85, maxSide: SCAN_MAX_SIDE },
    { quality: 0.73, maxSide: SCAN_MAX_SIDE },
    { quality: 0.61, maxSide: 1920 },
    { quality: 0.5, maxSide: 1536 },
];

/* Холст страницы после поворота на rotation (кратно 90°). */
export const turnedSize = (width, height, rotation) => {
    const turn = ((rotation % 360) + 360) % 360;
    return turn % 180 ? { width: height, height: width, turn } : { width, height, turn };
};

/* Что делает «назад» (жест телефона, Esc, «Отмена»/«Назад» в шапке).
   'stay' — файл собирается, выйти нельзя; 'close-confirm' — убрать вопрос
   «Удалить скан?»; 'leave-crop' — с шага «края» на камеру (или к страницам,
   если правили края снятой); 'to-review' — с камеры к снятым страницам;
   'confirm' — спросить «Удалить скан?»; 'close' — закрыть окно. */
export const scannerBack = ({ step, pages, busy, confirm }) => {
    if (busy === 'save') return 'stay';
    if (confirm) return 'close-confirm';
    if (step === 'crop') return 'leave-crop';
    if (step === 'camera' && pages > 0) return 'to-review';
    if (pages > 0) return 'confirm';
    return 'close';
};

/* Рамку можно оставить: выпуклая, из конечных чисел и не щель. */
export const cropUsable = (corners, width, height) => isConvexQuad(corners)
    && quadArea(corners) >= width * height * 0.005;

/* Страницы после «Оставить» на шаге «края». Новая — в конец; правка краёв уже
   снятой — на её место и с НОВЫМ id: превью кэшируются по id, и иначе
   показывалась бы прежняя обрезка. Углы — по порядку ЛВ, ПВ, ПН, ЛН: рамка,
   у которой человек поменял местами противоположные углы, выпуклая, но
   обходится против часовой — страница вышла бы зеркальной. */
export const keepScannedPage = (pages, shot, id) => {
    const corners = orderCorners(shot.corners);
    if (shot.editing !== null && shot.editing !== undefined) {
        return {
            pages: pages.map((page, index) => (index === shot.editing ? { ...page, id, corners } : page)),
            current: shot.editing,
        };
    }
    const page = { id, blob: shot.blob, url: shot.url, width: shot.width, height: shot.height, corners, rotation: 0 };
    return { pages: [...pages, page], current: pages.length };
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
    if (!corners.every((p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1]))) return false;
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
/*
 * Переписан 26.10.2026 после слов владельца «документ на столе он никак не
 * распознает». Замер на настоящих кадрах камеры — открытом наборе SmartDoc
 * 2015 (2555 кадров: лист A4 на пяти столах, углы листа размечены): прежний
 * поиск (серые границы Канни → прямые Хафа) находил лист верно в 54 % кадров,
 * на светлом офисном столе — ни разу, а в 15 % кадров ставил уверенную
 * неверную рамку. Причин три:
 *   — на светлом столе кромка листа почти не отличается по яркости (перепад
 *     3–5 из 255), зато хорошо видна по цвету: голубоватая бумага на тёплом
 *     столе. Серый кадр её не видел вовсе;
 *   — порог Канни «по большинству границ» задирали строки текста, и слабая
 *     кромка выпадала из границ целиком;
 *   — рамку из кабеля, края стола и строки текста ничто не проверяло на то,
 *     что внутри неё бумага, а снаружи стол.
 * Теперь: перепад цвета и яркости вместе → тонкие границы → отрезки по
 * направлению перепада (как LSD) → прямые из отрезков → четырёхугольник из
 * двух пар прямых, и у каждого — доводы: какая доля сторон лежит на кромке,
 * разный ли материал по обе стороны каждой стороны (и одинаково ли разный у
 * всех четырёх), не идут ли кромки дальше углов, светло ли внутри, близко ли к
 * середине кадра. На том же наборе: верно 90 % кадров (столы 1–4 — 98–100 %),
 * неверная рамка — 2 %. Стол, заваленный бумагами, кабелями и посудой,
 * остаётся трудным (верно ~10 %), но там поиск чаще молчит, чем ошибается.
 */

/* Сторона кадра, на котором ищется лист: живой поиск идёт несколько раз в
   секунду на телефоне, и больше пикселей точности почти не добавляют — углы
   снимка уточняет refineQuad на полном кадре. */
export const DETECT_SIDE = 320;

/* Кадр вдвое меньше — средним по квадрату 2×2. Кадр камеры рисуется в холст
   вдвое крупнее кадра поиска и сжимается здесь: drawImage, сжимая 4K-кадр
   разом в двенадцать раз, берёт по четыре соседних пикселя на точку, и строки
   текста рассыпаются в шум, а замер поиска шёл на честно усреднённых кадрах. */
export const halveRGBA = (data, w, h) => {
    const hw = w >> 1;
    const hh = h >> 1;
    const out = new Uint8ClampedArray(hw * hh * 4);
    for (let y = 0; y < hh; y += 1) {
        const r0 = 2 * y * w * 4;
        const r1 = r0 + w * 4;
        for (let x = 0; x < hw; x += 1) {
            const i = r0 + x * 8;
            const j = r1 + x * 8;
            const o = (y * hw + x) * 4;
            out[o] = (data[i] + data[i + 4] + data[j] + data[j + 4] + 2) >> 2;
            out[o + 1] = (data[i + 1] + data[i + 5] + data[j + 1] + data[j + 5] + 2) >> 2;
            out[o + 2] = (data[i + 2] + data[i + 6] + data[j + 2] + data[j + 6] + 2) >> 2;
            out[o + 3] = 255;
        }
    }
    return { data: out, width: hw, height: hh };
};

/* Уверенность рамки 0…1 — доводы на единицу периметра. На наборе SmartDoc:
   ниже MIN_CONFIDENCE поиск молчит (так он отсекает больше половины своих
   неверных рамок и почти не теряет верных); с SHOW_CONFIDENCE рамка видна в
   видоискателе (неверных — меньше 1 %); с AUTO_CONFIDENCE, неподвижная, она
   повод для автоснимка. */
export const MIN_CONFIDENCE = 0.4;
export const SHOW_CONFIDENCE = 0.6;
export const AUTO_CONFIDENCE = 0.9;
const CONFIDENCE_FULL = 0.4;

const angDiff = (a, b) => {
    const d = Math.abs(a - b) % Math.PI;
    return d > Math.PI / 2 ? Math.PI - d : d;
};

const blur3 = (src, w, h) => {
    const tmp = new Float32Array(w * h);
    const out = new Float32Array(w * h);
    for (let y = 0; y < h; y += 1) {
        const row = y * w;
        for (let x = 0; x < w; x += 1) {
            const x0 = x > 0 ? x - 1 : 0;
            const x2 = x < w - 1 ? x + 1 : w - 1;
            tmp[row + x] = (src[row + x0] + 2 * src[row + x] + src[row + x2]) / 4;
        }
    }
    for (let y = 0; y < h; y += 1) {
        const r0 = (y > 0 ? y - 1 : 0) * w;
        const r1 = y * w;
        const r2 = (y < h - 1 ? y + 1 : h - 1) * w;
        for (let x = 0; x < w; x += 1) out[r1 + x] = (tmp[r0 + x] + 2 * tmp[r1 + x] + tmp[r2 + x]) / 4;
    }
    return out;
};

/* Кадр для поиска: яркость L и две цветоразности (красный−зелёный A и
   жёлтый−синий B), чуть размытые (3×3), и в каждой точке — сила и направление
   перепада по трём каналам сразу (сумма тензоров структуры). Границы тонкие:
   остаётся гребень перепада поперёк кромки, как у Канни. С размытием 5×5
   кромка листа слипалась со строками текста у края в одну широкую полосу, и
   та отбрасывалась (замер, 26.10.2026). */
const MIN_EDGE = 0.5;
const prepareFrame = (rgba, w, h) => {
    const n = w * h;
    const L = new Float32Array(n);
    const A = new Float32Array(n);
    const B = new Float32Array(n);
    for (let i = 0, j = 0; i < n; i += 1, j += 4) {
        const r = rgba[j];
        const g = rgba[j + 1];
        const b = rgba[j + 2];
        L[i] = 0.299 * r + 0.587 * g + 0.114 * b;
        A[i] = r - g;
        B[i] = 0.5 * (r + g) - b;
    }
    const bl = blur3(L, w, h);
    const ba = blur3(A, w, h);
    const bb = blur3(B, w, h);
    const raw = new Float32Array(n);
    const ori = new Float32Array(n); // направление наибольшего перепада, 0…π
    const dir = new Uint8Array(n); // оно же четвертями — для утончения
    for (let y = 1; y < h - 1; y += 1) {
        for (let x = 1; x < w - 1; x += 1) {
            const i = y * w + x;
            const a = i - w - 1; const b = i - w; const c = i - w + 1;
            const d = i - 1; const e = i + 1;
            const g = i + w - 1; const k = i + w; const m = i + w + 1;
            const lx = (bl[c] + 2 * bl[e] + bl[m]) - (bl[a] + 2 * bl[d] + bl[g]);
            const ly = (bl[g] + 2 * bl[k] + bl[m]) - (bl[a] + 2 * bl[b] + bl[c]);
            const ax = (ba[c] + 2 * ba[e] + ba[m]) - (ba[a] + 2 * ba[d] + ba[g]);
            const ay = (ba[g] + 2 * ba[k] + ba[m]) - (ba[a] + 2 * ba[b] + ba[c]);
            const bx = (bb[c] + 2 * bb[e] + bb[m]) - (bb[a] + 2 * bb[d] + bb[g]);
            const by = (bb[g] + 2 * bb[k] + bb[m]) - (bb[a] + 2 * bb[b] + bb[c]);
            // ÷64: Собель даёт 8 × наклон, а здесь квадраты.
            const gxx = (lx * lx + ax * ax + bx * bx) / 64;
            const gyy = (ly * ly + ay * ay + by * by) / 64;
            const gxy = (lx * ly + ax * ay + bx * by) / 64;
            const dxy = gxx - gyy;
            const v = Math.sqrt(Math.max(0, (gxx + gyy + Math.sqrt(dxy * dxy + 4 * gxy * gxy)) / 2));
            raw[i] = v;
            if (v < MIN_EDGE) continue;
            let t = 0.5 * Math.atan2(2 * gxy, dxy);
            if (t < 0) t += Math.PI;
            ori[i] = t;
            // Граница четвертей в 22,5° по направлению — это |2·gxy| = |gxx − gyy|.
            const q2 = 2 * gxy;
            if ((dxy < 0 ? -dxy : dxy) >= (q2 < 0 ? -q2 : q2)) dir[i] = dxy >= 0 ? 0 : 2;
            else dir[i] = gxy > 0 ? 1 : 3;
        }
    }
    const mag = new Float32Array(n);
    for (let y = 1; y < h - 1; y += 1) {
        for (let x = 1; x < w - 1; x += 1) {
            const i = y * w + x;
            const v = raw[i];
            if (v < MIN_EDGE) continue;
            const q = dir[i];
            let m1;
            let m2;
            if (q === 0) { m1 = raw[i - 1]; m2 = raw[i + 1]; }
            else if (q === 2) { m1 = raw[i - w]; m2 = raw[i + w]; }
            else if (q === 1) { m1 = raw[i - w - 1]; m2 = raw[i + w + 1]; }
            else { m1 = raw[i - w + 1]; m2 = raw[i + w - 1]; }
            if (v >= m1 && v > m2) mag[i] = v;
        }
    }
    return { w, h, L: bl, A: ba, B: bb, mag, raw, ori, rho: 0 };
};

/* Отрезки: связные области тонких границ с одним направлением перепада
   (±22,5°), как у LSD. Порог силы — от шума кадра: у гладкого белого стола
   он низкий, и слабая кромка проходит, у деревянного — выше волокон. Ось
   отрезка — главная ось разброса его точек: средний наклон перепада ошибается
   на градус, и у длинной кромки «ширина» выходила 7 px — кромку отбрасывало.
   Ширина — среднеквадратичная, плотность — точек на длину: у тонкой линии под
   углом α их len·max(|cos α|, |sin α|). */
const SEG_TOLERANCE = (22.5 * Math.PI) / 180;
const SEG_RHO_MIN = 0.8;
const SEG_RHO_NOISE = 2.5;
const SEG_MIN_LEN = 7;
const SEG_MAX_RMS = 1.2;
const SEG_MIN_DENSITY = 0.6;
const EDGE_BORDER = 2;
const ORDER_BINS = 1024;
const findSegments = (f) => {
    const { w, h, mag, raw, ori } = f;
    const n = w * h;
    const sample = [];
    for (let i = 0; i < n; i += 7) sample.push(raw[i]);
    sample.sort((a, b) => a - b);
    const rho = Math.max(SEG_RHO_MIN, SEG_RHO_NOISE * (sample[sample.length >> 1] || 0));
    f.rho = rho;
    // Затравки — от сильных границ к слабым (сортировка корзинами).
    let maxMag = 0;
    for (let i = 0; i < n; i += 1) if (mag[i] > maxMag) maxMag = mag[i];
    const scale = maxMag > 0 ? (ORDER_BINS - 1) / maxMag : 0;
    const counts = new Int32Array(ORDER_BINS);
    for (let y = EDGE_BORDER; y < h - EDGE_BORDER; y += 1) {
        for (let x = EDGE_BORDER; x < w - EDGE_BORDER; x += 1) {
            const v = mag[y * w + x];
            if (v >= rho) counts[(v * scale) | 0] += 1;
        }
    }
    const fill = new Int32Array(ORDER_BINS);
    let total = 0;
    for (let b = ORDER_BINS - 1; b >= 0; b -= 1) { fill[b] = total; total += counts[b]; }
    const order = new Int32Array(total);
    for (let y = EDGE_BORDER; y < h - EDGE_BORDER; y += 1) {
        for (let x = EDGE_BORDER; x < w - EDGE_BORDER; x += 1) {
            const i = y * w + x;
            if (mag[i] >= rho) order[fill[(mag[i] * scale) | 0]++] = i;
        }
    }
    const used = new Uint8Array(n);
    const stack = new Int32Array(n);
    const region = new Int32Array(n);
    const segs = [];
    for (let k = 0; k < order.length; k += 1) {
        const seed = order[k];
        if (used[seed]) continue;
        used[seed] = 1;
        let size = 0;
        region[size++] = seed;
        let top = 0;
        stack[top++] = seed;
        let c2 = Math.cos(2 * ori[seed]);
        let s2 = Math.sin(2 * ori[seed]);
        let theta = ori[seed];
        while (top) {
            const p = stack[--top];
            const py = (p / w) | 0;
            const px = p - py * w;
            for (let dy = -1; dy <= 1; dy += 1) {
                const yy = py + dy;
                if (yy < EDGE_BORDER || yy >= h - EDGE_BORDER) continue;
                for (let dx = -1; dx <= 1; dx += 1) {
                    const xx = px + dx;
                    if ((!dx && !dy) || xx < EDGE_BORDER || xx >= w - EDGE_BORDER) continue;
                    const q = yy * w + xx;
                    if (used[q] || mag[q] < rho || angDiff(ori[q], theta) > SEG_TOLERANCE) continue;
                    used[q] = 1;
                    region[size++] = q;
                    stack[top++] = q;
                    c2 += Math.cos(2 * ori[q]);
                    s2 += Math.sin(2 * ori[q]);
                    theta = 0.5 * Math.atan2(s2, c2);
                    if (theta < 0) theta += Math.PI;
                }
            }
        }
        if (size < SEG_MIN_LEN) continue;
        let sw = 0;
        let sx = 0;
        let sy = 0;
        for (let r = 0; r < size; r += 1) {
            const p = region[r];
            const yy = (p / w) | 0;
            const v = mag[p];
            sw += v; sx += v * (p - yy * w); sy += v * yy;
        }
        const cx = sx / sw;
        const cy = sy / sw;
        let mxx = 0;
        let myy = 0;
        let mxy = 0;
        for (let r = 0; r < size; r += 1) {
            const p = region[r];
            const yy = (p / w) | 0;
            const ddx = p - yy * w - cx;
            const ddy = yy - cy;
            const v = mag[p];
            mxx += v * ddx * ddx; myy += v * ddy * ddy; mxy += v * ddx * ddy;
        }
        const axis = 0.5 * Math.atan2(2 * mxy, mxx - myy);
        const ux = Math.cos(axis);
        const uy = Math.sin(axis);
        let t0 = Infinity;
        let t1 = -Infinity;
        let ss = 0;
        for (let r = 0; r < size; r += 1) {
            const p = region[r];
            const yy = (p / w) | 0;
            const ddx = p - yy * w - cx;
            const ddy = yy - cy;
            const t = ddx * ux + ddy * uy;
            const s = -ddx * uy + ddy * ux;
            if (t < t0) t0 = t;
            if (t > t1) t1 = t;
            ss += s * s;
        }
        const len = t1 - t0 + 1;
        if (len < SEG_MIN_LEN || Math.sqrt(ss / size) > SEG_MAX_RMS) continue;
        if (size / (len * Math.max(Math.abs(ux), Math.abs(uy))) < SEG_MIN_DENSITY) continue;
        let normal = axis + Math.PI / 2;
        if (normal >= Math.PI) normal -= Math.PI;
        if (normal < 0) normal += Math.PI;
        segs.push({
            x0: cx + ux * t0, y0: cy + uy * t0, x1: cx + ux * t1, y1: cy + uy * t1,
            len, theta: normal, nx: Math.cos(normal), ny: Math.sin(normal),
        });
    }
    return segs;
};

/* Прямые в нормальной форме (нормаль nx, ny; rho — от центра кадра) из
   отрезков на одной линии: кромку листа рвут блик, тень, ручка поверх, и
   каждая её часть — свой отрезок. ivs — промежутки опоры вдоль прямой. */
const LINE_ANGLE = (3 * Math.PI) / 180;
const MAX_LINES = 30;
const refitLine = (line, cx, cy) => {
    let sw = 0;
    let sx = 0;
    let sy = 0;
    for (const s of line.segs) { sw += 2 * s.len; sx += s.len * (s.x0 + s.x1); sy += s.len * (s.y0 + s.y1); }
    const mx = sx / sw;
    const my = sy / sw;
    let sxx = 0;
    let syy = 0;
    let sxy = 0;
    for (const s of line.segs) {
        for (const [x, y] of [[s.x0, s.y0], [s.x1, s.y1]]) {
            sxx += s.len * (x - mx) ** 2; syy += s.len * (y - my) ** 2; sxy += s.len * (x - mx) * (y - my);
        }
    }
    const ang = 0.5 * Math.atan2(2 * sxy, sxx - syy);
    let nx = -Math.sin(ang);
    let ny = Math.cos(ang);
    if (nx * line.nx + ny * line.ny < 0) { nx = -nx; ny = -ny; }
    let theta = Math.atan2(ny, nx);
    if (theta < 0) theta += Math.PI;
    if (theta >= Math.PI) theta -= Math.PI;
    Object.assign(line, { nx, ny, theta, rho: (mx - cx) * nx + (my - cy) * ny });
};
const mergeLines = (segs, w, h) => {
    const cx = w / 2;
    const cy = h / 2;
    const near = Math.max(1.5, 0.008 * Math.hypot(w, h));
    const lines = [];
    for (const s of [...segs].sort((a, b) => b.len - a.len)) {
        const line = lines.find((item) => angDiff(item.theta, s.theta) <= LINE_ANGLE * (s.len < 15 ? 2 : 1)
            && Math.abs((s.x0 - cx) * item.nx + (s.y0 - cy) * item.ny - item.rho) <= near
            && Math.abs((s.x1 - cx) * item.nx + (s.y1 - cy) * item.ny - item.rho) <= near);
        if (line) {
            line.segs.push(s);
            refitLine(line, cx, cy);
        } else {
            lines.push({ theta: s.theta, nx: s.nx, ny: s.ny, rho: (s.x0 - cx) * s.nx + (s.y0 - cy) * s.ny, segs: [s] });
        }
    }
    for (const line of lines) {
        const ux = -line.ny;
        const uy = line.nx;
        const spans = line.segs.map((s) => {
            const a = (s.x0 - cx) * ux + (s.y0 - cy) * uy;
            const b = (s.x1 - cx) * ux + (s.y1 - cy) * uy;
            return a < b ? [a, b] : [b, a];
        }).sort((p, q) => p[0] - q[0]);
        const ivs = [];
        for (const span of spans) {
            const last = ivs[ivs.length - 1];
            if (last && span[0] <= last[1] + 1) last[1] = Math.max(last[1], span[1]);
            else ivs.push([...span]);
        }
        line.ivs = ivs;
        line.cover = ivs.reduce((sum, [a, b]) => sum + b - a, 0);
    }
    return lines.sort((a, b) => b.cover - a.cover).slice(0, MAX_LINES);
};

const crossNormal = (a, b, cx, cy) => {
    const det = a.nx * b.ny - a.ny * b.nx;
    if (Math.abs(det) < 0.2) return null;
    return [(a.rho * b.ny - b.rho * a.ny) / det + cx, (b.rho * a.nx - a.rho * b.nx) / det + cy];
};

const lineThroughPoints = (a, b, cx, cy) => {
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    const nx = -(b[1] - a[1]) / len;
    const ny = (b[0] - a[0]) / len;
    let theta = Math.atan2(ny, nx);
    if (theta < 0) theta += Math.PI;
    if (theta >= Math.PI) theta -= Math.PI;
    return { nx, ny, theta, rho: (a[0] - cx) * nx + (a[1] - cy) * ny, ivs: [], cover: 0, fitted: true };
};

const anglesOk = (q) => {
    for (let i = 0; i < 4; i += 1) {
        const p = q[(i + 3) % 4];
        const c = q[i];
        const nn = q[(i + 1) % 4];
        const ax = p[0] - c[0];
        const ay = p[1] - c[1];
        const bx = nn[0] - c[0];
        const by = nn[1] - c[1];
        const cos = (ax * bx + ay * by) / (Math.hypot(ax, ay) * Math.hypot(bx, by) || 1);
        if (cos > 0.82 || cos < -0.82) return false; // угол вне 35°…145°
    }
    return true;
};

// Длина пересечения промежутков опоры с отрезком [a, b].
const coveredLength = (ivs, a, b) => {
    let sum = 0;
    for (const [s, e] of ivs) {
        const lo = Math.max(s, a);
        const hi = Math.min(e, b);
        if (hi > lo) sum += hi - lo;
    }
    return sum;
};

// Видимая часть стороны p→q — промежуток параметра t ∈ [0, 1] внутри кадра.
const visibleRange = (p, q, w, h) => {
    let t0 = 0;
    let t1 = 1;
    const m = EDGE_BORDER;
    // Ограничение вида num + den·t ≥ 0.
    for (const [num, den] of [[p[0] - m, q[0] - p[0]], [w - m - p[0], p[0] - q[0]], [p[1] - m, q[1] - p[1]], [h - m - p[1], p[1] - q[1]]]) {
        if (Math.abs(den) < 1e-9) {
            if (num < 0) return null;
        } else if (den > 0) t0 = Math.max(t0, -num / den);
        else t1 = Math.min(t1, -num / den);
    }
    return t1 > t0 ? [t0, t1] : null;
};

const sampleAt = (arr, w, h, x, y) => {
    if (x < 0 || y < 0 || x > w - 1 || y > h - 1) return NaN;
    const x0 = Math.floor(x);
    const y0 = Math.floor(y);
    const x1 = Math.min(w - 1, x0 + 1);
    const y1 = Math.min(h - 1, y0 + 1);
    const fx = x - x0;
    const fy = y - y0;
    return (arr[y0 * w + x0] * (1 - fx) + arr[y0 * w + x1] * fx) * (1 - fy)
        + (arr[y1 * w + x0] * (1 - fx) + arr[y1 * w + x1] * fx) * fy;
};

const quadCenter = (q) => [(q[0][0] + q[1][0] + q[2][0] + q[3][0]) / 4, (q[0][1] + q[1][1] + q[2][1] + q[3][1]) / 4];

/* Кандидат по углам ring и прямым сторон: видимая часть каждой стороны,
   какая её доля лежит на кромке (cover) и сколько кромки идёт дальше углов
   (over). У сторон, подогнанных по перепаду (fitted), своих отрезков нет — их
   опора меряется прямо по кадру. */
const CORNER_REACH = 0.15;
const MIN_AREA = 0.03;
const SIDE_MIN_COVER = 0.25;
const MEAN_MIN_COVER = 0.45;
const buildCandidate = (f, ring, sideLines) => {
    const { w, h } = f;
    const cx = w / 2;
    const cy = h / 2;
    if (ring.some(([x, y]) => x < -CORNER_REACH * w || y < -CORNER_REACH * h || x > (1 + CORNER_REACH) * w || y > (1 + CORNER_REACH) * h)) return null;
    if (!isConvexQuad(ring) || quadArea(ring) < w * h * MIN_AREA || !anglesOk(ring)) return null;
    let score = 0;
    let coverSum = 0;
    const sides = [];
    for (let k = 0; k < 4; k += 1) {
        const p = ring[k];
        const q = ring[(k + 1) % 4];
        const line = sideLines[k];
        const range = visibleRange(p, q, w, h);
        const sideLen = dist(p, q);
        if (!range || sideLen * (range[1] - range[0]) < Math.min(w, h) * 0.1) return null;
        let cover;
        let over = 0;
        const vis = sideLen * (range[1] - range[0]);
        if (line.fitted) {
            cover = sampledCover(f, p, q, range);
        } else {
            const ux = -line.ny;
            const uy = line.nx;
            const tp = (p[0] - cx) * ux + (p[1] - cy) * uy;
            const tq = (q[0] - cx) * ux + (q[1] - cy) * uy;
            const ta = tp + (tq - tp) * range[0];
            const tb = tp + (tq - tp) * range[1];
            const lo = Math.min(ta, tb);
            const hi = Math.max(ta, tb);
            cover = coveredLength(line.ivs, lo, hi) / vis;
            const ext = 0.25 * sideLen;
            over = (coveredLength(line.ivs, lo - ext, lo) + coveredLength(line.ivs, hi, hi + ext)) / (2 * ext);
        }
        if (cover < SIDE_MIN_COVER) return null;
        sides.push({ cover, over, range });
        score += vis * cover;
        coverSum += cover;
    }
    if (coverSum / 4 < MEAN_MIN_COVER) return null;
    return { ring, sideLines, sides, score, cover: coverSum / 4 };
};

/* Опора стороны прямо по кадру: доля точек, у которых рядом (±1,5 px) есть
   перепад того же направления. */
const SAMPLE_TOLERANCE = (25 * Math.PI) / 180;
const sampledCover = (f, p, q, range) => {
    const { w, h, raw, ori } = f;
    const len = dist(p, q);
    const nx = -(q[1] - p[1]) / len;
    const ny = (q[0] - p[0]) / len;
    let normal = Math.atan2(ny, nx);
    if (normal < 0) normal += Math.PI;
    if (normal >= Math.PI) normal -= Math.PI;
    const low = 0.7 * f.rho;
    const steps = Math.max(4, Math.round((len * (range[1] - range[0])) / 1.2));
    let hits = 0;
    for (let s = 0; s <= steps; s += 1) {
        const t = range[0] + ((range[1] - range[0]) * s) / steps;
        const x = p[0] + (q[0] - p[0]) * t;
        const y = p[1] + (q[1] - p[1]) * t;
        for (let d = -1.5; d <= 1.5; d += 0.5) {
            const xx = Math.round(x + nx * d);
            const yy = Math.round(y + ny * d);
            if (xx < 1 || yy < 1 || xx >= w - 1 || yy >= h - 1) continue;
            const i = yy * w + xx;
            if (raw[i] >= low && angDiff(ori[i], normal) < SAMPLE_TOLERANCE) { hits += 1; break; }
        }
    }
    return hits / (steps + 1);
};

/* Прямая кромки около отрезка a→b: поперёк него ищется наибольший перепад
   цвета (ближний — с поправкой на расстояние), по найденным точкам — прямая с
   отбросом выбросов. */
const fitEdgeLine = (f, a, b, center, search) => {
    const { w, h } = f;
    const len = dist(a, b);
    if (len < 8) return null;
    let nx = -(b[1] - a[1]) / len;
    let ny = (b[0] - a[0]) / len;
    if ((center[0] - (a[0] + b[0]) / 2) * nx + (center[1] - (a[1] + b[1]) / 2) * ny < 0) { nx = -nx; ny = -ny; }
    const pts = [];
    const n = Math.floor(len);
    for (let s = 0; s <= n; s += 1) {
        const t = s / n;
        if (t < 0.08 || t > 0.92) continue;
        const bx = a[0] + (b[0] - a[0]) * t;
        const by = a[1] + (b[1] - a[1]) * t;
        let best = 0;
        let bestD = 0;
        for (let d = -search; d <= search; d += 0.5) {
            const xi = bx + nx * (d + 0.75);
            const yi = by + ny * (d + 0.75);
            const xo = bx + nx * (d - 0.75);
            const yo = by + ny * (d - 0.75);
            const dl = sampleAt(f.L, w, h, xi, yi) - sampleAt(f.L, w, h, xo, yo);
            if (Number.isNaN(dl)) continue;
            const da = sampleAt(f.A, w, h, xi, yi) - sampleAt(f.A, w, h, xo, yo);
            const db = sampleAt(f.B, w, h, xi, yi) - sampleAt(f.B, w, h, xo, yo);
            const v = Math.hypot(dl, da, db) * Math.exp(-(d * d) / (2 * search * search));
            if (v > best) { best = v; bestD = d; }
        }
        if (best > 1) pts.push([bx + nx * bestD, by + ny * bestD, best]);
    }
    const line = robustLine(pts, 2.5, 1.5);
    if (!line) return null;
    return lineThroughPoints([line.mx, line.my], [line.mx + line.ux * 10, line.my + line.uy * 10], w / 2, h / 2);
};

/* Достройка: сторона k переносится туда, где кончаются кромки двух соседних
   сторон. Внутренняя линия документа (нижний край фотографии в журнале,
   линейка таблицы во всю ширину) рамку резала — а кромки листа идут через неё
   дальше, до настоящего угла (замер на крупных кадрах SmartDoc: верных на
   светлом столе 80 % → 87 %). */
const REPAIR_MIN_REACH = 0.06;
const supportEnd = (line, corner, along, cx, cy, gap) => {
    const ux = -line.ny;
    const uy = line.nx;
    const sign = along[0] * ux + along[1] * uy >= 0 ? 1 : -1;
    const tc = (corner[0] - cx) * ux + (corner[1] - cy) * uy;
    let cur = sign * tc;
    const spans = line.ivs.map(([a, b]) => (sign > 0 ? [a, b] : [-b, -a])).sort((p, q) => p[0] - q[0]);
    for (const [a, b] of spans) {
        if (b <= cur) continue;
        if (a > cur + gap) break;
        cur = Math.max(cur, b);
    }
    const t = sign * cur;
    return { point: [cx + line.nx * line.rho + ux * t, cy + line.ny * line.rho + uy * t], reach: Math.abs(t - tc) };
};
const repairCandidate = (f, cand) => {
    const { w, h } = f;
    const cx = w / 2;
    const cy = h / 2;
    const out = [];
    const lines = cand.sideLines;
    for (let k = 0; k < 4; k += 1) {
        const prev = lines[(k + 3) % 4];
        const next = lines[(k + 1) % 4];
        if (prev.fitted || next.fitted) continue;
        const a = cand.ring[k];
        const b = cand.ring[(k + 1) % 4];
        const pa = cand.ring[(k + 3) % 4];
        const nb = cand.ring[(k + 2) % 4];
        const la = dist(pa, a) || 1;
        const lb = dist(nb, b) || 1;
        const ea = supportEnd(prev, a, [(a[0] - pa[0]) / la, (a[1] - pa[1]) / la], cx, cy, Math.max(4, 0.08 * la));
        const eb = supportEnd(next, b, [(b[0] - nb[0]) / lb, (b[1] - nb[1]) / lb], cx, cy, Math.max(4, 0.08 * lb));
        if (ea.reach < Math.max(3, REPAIR_MIN_REACH * la) && eb.reach < Math.max(3, REPAIR_MIN_REACH * lb)) continue;
        const side = fitEdgeLine(f, ea.point, eb.point, quadCenter(cand.ring), 4) || lineThroughPoints(ea.point, eb.point, cx, cy);
        const sideLines = lines.map((line, i) => (i === k ? side : line));
        const ring = [];
        for (let i = 0; i < 4; i += 1) {
            const pt = crossNormal(sideLines[(i + 3) % 4], sideLines[i], cx, cy);
            if (pt) ring.push(pt);
        }
        const fixed = ring.length === 4 ? buildCandidate(f, ring, sideLines) : null;
        if (fixed) out.push(fixed);
    }
    return out;
};

/* Цвет поперёк стороны на offset пикселей внутрь и наружу: D — медиана
   разницы «внутри − снаружи» по L, A, B, dE — её величина, O — цвет снаружи. */
const SIDE_OFFSET = 3;
const sideColors = (f, p, q, center, range) => {
    const { w, h } = f;
    const len = dist(p, q) || 1;
    let nx = -(q[1] - p[1]) / len;
    let ny = (q[0] - p[0]) / len;
    if ((center[0] - (p[0] + q[0]) / 2) * nx + (center[1] - (p[1] + q[1]) / 2) * ny < 0) { nx = -nx; ny = -ny; }
    const dl = [];
    const da = [];
    const db = [];
    const ol = [];
    const oa = [];
    const ob = [];
    const steps = Math.max(6, Math.round((len * (range[1] - range[0])) / 3));
    for (let s = 0; s <= steps; s += 1) {
        const t = range[0] + (range[1] - range[0]) * (0.08 + (0.84 * s) / steps);
        const x = p[0] + (q[0] - p[0]) * t;
        const y = p[1] + (q[1] - p[1]) * t;
        const xi = x + nx * SIDE_OFFSET;
        const yi = y + ny * SIDE_OFFSET;
        const xo = x - nx * SIDE_OFFSET;
        const yo = y - ny * SIDE_OFFSET;
        const li = sampleAt(f.L, w, h, xi, yi);
        const lo = sampleAt(f.L, w, h, xo, yo);
        if (Number.isNaN(li) || Number.isNaN(lo)) continue;
        const ao = sampleAt(f.A, w, h, xo, yo);
        const bo = sampleAt(f.B, w, h, xo, yo);
        dl.push(li - lo);
        da.push(sampleAt(f.A, w, h, xi, yi) - ao);
        db.push(sampleAt(f.B, w, h, xi, yi) - bo);
        ol.push(lo); oa.push(ao); ob.push(bo);
    }
    if (!dl.length) return { D: [0, 0, 0], dE: 0, O: [0, 0, 0] };
    const median = (arr) => arr.sort((a, b) => a - b)[arr.length >> 1];
    const D = [median(dl), median(da), median(db)];
    return { D, dE: Math.hypot(D[0], D[1], D[2]), O: [median(ol), median(oa), median(ob)] };
};

/* Яркость кадра и внутренности кандидата (сетка 11×11 точек). paper — доля
   точек цвета бумаги: не темнее «белого» этого листа (его 90-й доли) больше
   чем на 12 %. У листа с текстом это больше половины, у клавиатуры (тёмные
   клавиши, светлые щели) — около трети. */
const frameLight = (f) => {
    const { w, h, L } = f;
    const vals = [];
    for (let y = 2; y < h - 2; y += 3) for (let x = 2; x < w - 2; x += 3) vals.push(L[y * w + x]);
    vals.sort((a, b) => a - b);
    return { q50: vals[vals.length >> 1] || 0, q95: vals[Math.floor(vals.length * 0.95)] || 0 };
};
const insideLight = (f, ring) => {
    const { w, h, L } = f;
    const [a, b, c, d] = ring;
    const vals = [];
    for (let i = 1; i < 12; i += 1) {
        for (let j = 1; j < 12; j += 1) {
            const u = i / 12;
            const v = j / 12;
            const x = (1 - u) * (1 - v) * a[0] + u * (1 - v) * b[0] + u * v * c[0] + (1 - u) * v * d[0];
            const y = (1 - u) * (1 - v) * a[1] + u * (1 - v) * b[1] + u * v * c[1] + (1 - u) * v * d[1];
            if (x >= 0 && y >= 0 && x <= w - 1 && y <= h - 1) vals.push(L[Math.round(y) * w + Math.round(x)]);
        }
    }
    if (!vals.length) return { q75: 0, paper: 0 };
    vals.sort((p, q) => p - q);
    const top = vals[Math.floor(vals.length * 0.9)];
    const lim = top - Math.max(10, 0.12 * top);
    return { q75: vals[Math.floor(vals.length * 0.75)], paper: vals.filter((v) => v >= lim).length / vals.length };
};

const ramp = (x, lo, hi) => Math.min(1, Math.max(0, (x - lo) / (hi - lo)));

/* Итоговая оценка кандидата: опора сторон × доводы.
   — Материал. Внутри стороны один материал (лист), снаружи другой (стол), и у
     всех четырёх сторон разница «внутри − снаружи» смотрит в одну сторону. Знак
     не задан: голубоватый лист бывает «цветнее» тёплого стола, а светлый стол —
     светлее листа. Сторону с тёмной фотографией у края выручает то, что
     снаружи у неё то же, что у остальных сторон, — стол.
   — Углы. Кромка, идущая дальше угла, — признак, что угол не настоящий (рамку
     режет линия внутри документа или край стола).
   — Внутри светло и много точек цвета бумаги; рамка ближе к середине кадра:
     на лист наводят, журнал и клавиатура у края кадра — соседи. */
const verifyCandidate = (f, cand, light) => {
    const { w, h } = f;
    const center = quadCenter(cand.ring);
    const sides = cand.sides.map((side, k) => sideColors(f, cand.ring[k], cand.ring[(k + 1) % 4], center, side.range));
    const sum = [0, 0, 0];
    for (const c of sides) if (c.dE > 0) for (let j = 0; j < 3; j += 1) sum[j] += c.D[j] / c.dE;
    const norm = Math.hypot(sum[0], sum[1], sum[2]) || 1;
    let material = 1;
    let corners = 1;
    let strong = 0;
    sides.forEach((c, k) => {
        const agree = c.dE > 0 ? (c.D[0] * sum[0] + c.D[1] * sum[1] + c.D[2] * sum[2]) / (c.dE * norm) : 0;
        const others = sides.filter((_, j) => j !== k);
        const outside = [0, 1, 2].map((j) => others.reduce((acc, o) => acc + o.O[j], 0) / 3);
        const spread = Math.max(6, others.reduce((acc, o) => acc + o.dE, 0) / 3);
        const sameOutside = Math.max(0, 1 - Math.hypot(c.O[0] - outside[0], c.O[1] - outside[1], c.O[2] - outside[2]) / (0.6 * spread));
        const r = ramp(c.dE, 3, 12) * Math.max(ramp(agree, 0.3, 0.9), sameOutside);
        if (r > 0.5) strong += 1;
        material *= 0.3 + 0.7 * r;
        corners *= Math.max(0, 1 - cand.sides[k].over) ** 1.5;
    });
    const inner = insideLight(f, cand.ring);
    const white = 0.6 + 0.4 * ramp(inner.q75 - light.q50, 0, Math.max(8, light.q95 - light.q50));
    const paper = 0.3 + 0.7 * ramp(inner.paper, 0.3, 0.6);
    const off = dist(center, [w / 2, h / 2]) / (0.5 * Math.hypot(w, h));
    const middle = 1 - 0.5 * Math.min(1, off * off);
    cand.strong = strong;
    cand.final = cand.score * material * corners * white * paper * middle;
    return cand;
};

/* Лист на кадре RGBA rgba (w×h): { corners: [ЛВ, ПВ, ПН, ЛН], confidence,
   coverage } или null. Каждый кадр — заново, без рамки прошлого кадра:
   подогнанная к кромкам этого кадра прошлая рамка прилипала к соседней
   кромке посильнее (столбец фотографий у края листа, край фото в журнале) и
   держала сама себя — на стенде вся пауза перед автоснимком стояла с ошибкой
   угла 15–18 px, а точности на наборе SmartDoc она не прибавляла. От мигания
   рамки в видоискателе бережёт createQuadTracker.
   Углы — в координатах холста (пиксель i — отрезок [i, i+1]), как у SVG,
   drawImage и warpPerspective; внутри поиск считает центр пикселя целым.
   Без поправки на полпикселя рамка с кадра поиска на 4K-снимке съезжала бы на
   6 px влево-вверх. */
const PIXEL_CENTER = 0.5;
const toIndex = (corners) => corners.map(([x, y]) => [x - PIXEL_CENTER, y - PIXEL_CENTER]);
const fromIndex = (corners) => corners.map(([x, y]) => [x + PIXEL_CENTER, y + PIXEL_CENTER]);
const VERIFY_TOP = 16;
const REPAIR_TOP = 4;
const REPAIR_MARGIN = 1.1;
export const detectDocument = (rgba, w, h) => {
    if (!rgba || w < 32 || h < 32 || rgba.length < w * h * 4) return null;
    const f = prepareFrame(rgba, w, h);
    const lines = mergeLines(findSegments(f), w, h);
    const cx = w / 2;
    const cy = h / 2;
    const pairs = [];
    for (let i = 0; i < lines.length; i += 1) {
        for (let j = i + 1; j < lines.length; j += 1) {
            const a = lines[i];
            const b = lines[j];
            // Противоположные стороны в сильной перспективе расходятся до ~35°.
            if (angDiff(a.theta, b.theta) > (40 * Math.PI) / 180) continue;
            const gap = Math.abs(a.nx * b.nx + a.ny * b.ny >= 0 ? a.rho - b.rho : a.rho + b.rho);
            if (gap >= Math.min(w, h) * 0.12) pairs.push([a, b]);
        }
    }
    const cands = [];
    for (let i = 0; i < pairs.length; i += 1) {
        for (let j = i + 1; j < pairs.length; j += 1) {
            const [a1, a2] = pairs[i];
            const [b1, b2] = pairs[j];
            if (angDiff(a1.theta, b1.theta) < Math.PI / 4) continue;
            const ring = [crossNormal(a1, b1, cx, cy), crossNormal(a1, b2, cx, cy), crossNormal(a2, b2, cx, cy), crossNormal(a2, b1, cx, cy)];
            if (ring.some((p) => !p)) continue;
            const cand = buildCandidate(f, ring, [a1, b2, a2, b1]);
            if (cand) cands.push(cand);
        }
    }
    const light = frameLight(f);
    const verified = [];
    let best = null;
    cands.sort((a, b) => b.score - a.score);
    for (const cand of cands.slice(0, VERIFY_TOP)) {
        verified.push(verifyCandidate(f, cand, light));
        if (!best || cand.final > best.final) best = cand;
    }
    verified.sort((a, b) => b.final - a.final);
    // Достроенная рамка побеждает с запасом: почти равная найденной она —
    // та же рамка с одной стороной, подогнанной к соседней кромке (край
    // заголовка у верха журнала), а не срезанная, ради которой достройка.
    const found = best;
    for (const cand of verified.slice(0, REPAIR_TOP)) {
        for (const fixed of repairCandidate(f, cand)) {
            verifyCandidate(f, fixed, light);
            if (fixed.final > best.final && fixed.final > found.final * REPAIR_MARGIN) best = fixed;
        }
    }
    if (!best || !(best.final > 0) || best.strong < 3) return null;
    let perimeter = 0;
    for (let k = 0; k < 4; k += 1) perimeter += dist(best.ring[k], best.ring[(k + 1) % 4]);
    const confidence = Math.min(1, best.final / perimeter / CONFIDENCE_FULL);
    if (confidence < MIN_CONFIDENCE) return null;
    return { corners: fromIndex(orderCorners(best.ring)), confidence, coverage: best.cover };
};

/* ── Уточнение рамки ──────────────────────────────────────────────────── */

const robustLine = (pts, wide, narrow) => {
    const fit = (weights) => {
        let sw = 0;
        let sx = 0;
        let sy = 0;
        pts.forEach(([x, y], i) => { sw += weights[i]; sx += weights[i] * x; sy += weights[i] * y; });
        if (sw <= 0) return null;
        const mx = sx / sw;
        const my = sy / sw;
        let sxx = 0;
        let syy = 0;
        let sxy = 0;
        pts.forEach(([x, y], i) => {
            sxx += weights[i] * (x - mx) ** 2; syy += weights[i] * (y - my) ** 2; sxy += weights[i] * (x - mx) * (y - my);
        });
        const ang = 0.5 * Math.atan2(2 * sxy, sxx - syy);
        return { mx, my, ux: Math.cos(ang), uy: Math.sin(ang) };
    };
    if (pts.length < 6) return null;
    let line = fit(pts.map((p) => p[2]));
    // Выбросы (строка текста у края, тень) теряют вес по расстоянию до прямой.
    for (let it = 0; it < 3 && line; it += 1) {
        const lim = it === 0 ? wide : narrow;
        const current = line;
        line = fit(pts.map(([x, y, wt]) => {
            const r = Math.abs((x - current.mx) * -current.uy + (y - current.my) * current.ux) / lim;
            return r < 1 ? wt * (1 - r * r) ** 2 : 0;
        })) || current;
    }
    return line;
};

/* Углы снимка с долей пикселя (rgba — тот же кадр крупнее, углы в его
   пикселях). Каждая сторона притягивается к своей кромке: поперёк стороны
   ищется наибольший перепад цвета — в ту же сторону, что у этой кромки в
   целом, и ближе к исходной прямой (так не перескакивает на строку текста у
   края или на тень листа); по точкам — прямая с отбросом выбросов, углы —
   пересечения прямых. search — полуширина поиска в пикселях этого кадра. На
   кадрах SmartDoc 960×540 (поиск на 320): медианная ошибка угла 2,5 → 1,4 px,
   у 90 % углов — не больше 3,6 px вместо 4,5. Если сторону не на что
   опереть, углы остаются прежними. */
const sampleColor = (rgba, w, h, x, y, out) => {
    const cx = Math.min(w - 1, Math.max(0, x));
    const cy = Math.min(h - 1, Math.max(0, y));
    const x0 = Math.floor(cx);
    const y0 = Math.floor(cy);
    const x1 = Math.min(w - 1, x0 + 1);
    const y1 = Math.min(h - 1, y0 + 1);
    const fx = cx - x0;
    const fy = cy - y0;
    const i00 = (y0 * w + x0) * 4;
    const i10 = (y0 * w + x1) * 4;
    const i01 = (y1 * w + x0) * 4;
    const i11 = (y1 * w + x1) * 4;
    const w00 = (1 - fx) * (1 - fy);
    const w10 = fx * (1 - fy);
    const w01 = (1 - fx) * fy;
    const w11 = fx * fy;
    const r = rgba[i00] * w00 + rgba[i10] * w10 + rgba[i01] * w01 + rgba[i11] * w11;
    const g = rgba[i00 + 1] * w00 + rgba[i10 + 1] * w10 + rgba[i01 + 1] * w01 + rgba[i11 + 1] * w11;
    const b = rgba[i00 + 2] * w00 + rgba[i10 + 2] * w10 + rgba[i01 + 2] * w01 + rgba[i11 + 2] * w11;
    out[0] = 0.299 * r + 0.587 * g + 0.114 * b;
    out[1] = r - g;
    out[2] = 0.5 * (r + g) - b;
};
export const refineQuad = (rgba, w, h, input, { search = 3 } = {}) => {
    if (!isConvexQuad(input)) return input;
    const corners = toIndex(input);
    const reach = Math.max(1, Math.round(search));
    const size = 2 * reach + 3;
    const pl = new Float32Array(size);
    const pa = new Float32Array(size);
    const pb = new Float32Array(size);
    const color = [0, 0, 0];
    const center = quadCenter(corners);
    const sigma = Math.max(1.5, reach / 2);
    // Профили поперёк каждой стороны: производная цвета снаружи → внутрь.
    const sides = [];
    for (let k = 0; k < 4; k += 1) {
        const p = corners[k];
        const q = corners[(k + 1) % 4];
        const len = dist(p, q);
        if (len < 8) return input;
        let nx = -(q[1] - p[1]) / len;
        let ny = (q[0] - p[0]) / len;
        if ((center[0] - (p[0] + q[0]) / 2) * nx + (center[1] - (p[1] + q[1]) / 2) * ny < 0) { nx = -nx; ny = -ny; }
        // Длинную сторону большого снимка хватает пройти через пиксель.
        const step = Math.max(1, len / 600);
        const profiles = [];
        for (let s = 0; s <= len; s += step) {
            const t = s / len;
            if (t < 0.06 || t > 0.94) continue;
            const bx = p[0] + (q[0] - p[0]) * t;
            const by = p[1] + (q[1] - p[1]) * t;
            if (bx < 1 || by < 1 || bx > w - 2 || by > h - 2) continue;
            for (let d = -reach - 1; d <= reach + 1; d += 1) {
                sampleColor(rgba, w, h, bx + nx * d, by + ny * d, color);
                pl[d + reach + 1] = color[0];
                pa[d + reach + 1] = color[1];
                pb[d + reach + 1] = color[2];
            }
            const der = [];
            for (let i = 1; i <= 2 * reach + 1; i += 1) der.push([pl[i + 1] - pl[i - 1], pa[i + 1] - pa[i - 1], pb[i + 1] - pb[i - 1]]);
            profiles.push({ bx, by, der });
        }
        if (profiles.length < 6) return input;
        sides.push({ nx, ny, profiles });
    }
    /* Направление перемены цвета «стол → лист» — общее для четырёх сторон:
       у каждой стороны — сумма производных у исходной прямой, и голос
       каждой стороны равный. Сторона, легшая на край заголовка или фото
       внутри листа («бумага → тёмное»), смотрит против остальных и одна их
       не перетянет: её пики противоположного знака отбрасываются, и она
       ищет настоящую кромку (журнал на стенде: угол уходил на 16 px). */
    const ref = [0, 0, 0];
    for (const { profiles } of sides) {
        const own = [0, 0, 0];
        for (const { der } of profiles) {
            for (let i = reach - 1; i <= reach + 1; i += 1) for (let j = 0; j < 3; j += 1) own[j] += der[i][j];
        }
        const n = Math.hypot(own[0], own[1], own[2]) || 1;
        for (let j = 0; j < 3; j += 1) ref[j] += own[j] / n;
    }
    const refLen = Math.hypot(ref[0], ref[1], ref[2]) || 1;
    const lines = [];
    for (const { nx, ny, profiles } of sides) {
        const pts = [];
        for (const { bx, by, der } of profiles) {
            // Какой пик брать — решает близость к исходной прямой; где он — сам
            // перепад: вес близости, умноженный до уточнения, тянул пик к
            // исходной прямой на треть пикселя.
            const vals = der.map((v) => Math.max(0, (v[0] * ref[0] + v[1] * ref[1] + v[2] * ref[2]) / refLen));
            let best = -1;
            let bestW = 0;
            vals.forEach((v, i) => {
                const d = i - reach;
                const weighted = v * Math.exp(-(d * d) / (2 * sigma * sigma));
                if (weighted > bestW) { bestW = weighted; best = i; }
            });
            while (best > 0 && vals[best - 1] > vals[best]) best -= 1;
            while (best >= 0 && best < vals.length - 1 && vals[best + 1] > vals[best]) best += 1;
            if (best < 1 || best > vals.length - 2 || vals[best] < 1) continue;
            const a = vals[best - 1];
            const b = vals[best + 1];
            const peak = vals[best];
            const den = a - 2 * peak + b;
            const d = best - reach + (den < 0 ? (0.5 * (a - b)) / den : 0);
            pts.push([bx + nx * d, by + ny * d, peak]);
        }
        const line = robustLine(pts, Math.max(2.5, 0.8 * reach), Math.max(1.5, 0.5 * reach));
        if (!line) return input;
        lines.push(line);
    }
    const out = [];
    for (let k = 0; k < 4; k += 1) {
        const a = lines[(k + 3) % 4];
        const b = lines[k];
        const det = a.ux * b.uy - a.uy * b.ux;
        if (Math.abs(det) < 1e-6) return input;
        const t = ((b.mx - a.mx) * b.uy - (b.my - a.my) * b.ux) / det;
        const pt = [a.mx + a.ux * t, a.my + a.uy * t];
        // Поправка, а не новый поиск: далеко уехавший угол — признак, что
        // сторона прилипла не к той кромке.
        if (!Number.isFinite(pt[0]) || !Number.isFinite(pt[1]) || dist(pt, corners[k]) > reach * 3) return input;
        out.push(pt);
    }
    return isConvexQuad(out) ? fromIndex(out) : input;
};

/* ── Живая рамка и автоснимок ─────────────────────────────────────────── */

/* Живая рамка в видоискателе: не мигает от кадра к кадру. Новое положение
   принимается после двух согласных кадров, близкое — подтягивается на
   FOLLOW пути (половина отставала от листа в руке на полсекунды — рамку плавно
   ведёт ещё и экран), рамка гаснет после нескольких кадров без листа. */
const FOLLOW = 0.7;
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
                shown = shown.map((p, i) => [p[0] + (quad[i][0] - p[0]) * FOLLOW, p[1] + (quad[i][1] - p[1]) * FOLLOW]);
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

/* Автоснимок — режим «Авто», как у сканера iOS: страница снимается сама,
   когда лист найден уверенно, занимает заметную часть кадра и рамка стоит на
   месте AUTO_HOLD_MS. Потом — пауза до следующего листа: рамка пропала (лист
   убрали, накрыли рукой, перевернули) или уехала далеко. Иначе тот же лист
   снимался бы раз в секунду.

   push(found, w, h, now) → { state, progress }:
     'search' — листа нет;   'closer' — лист мелкий, поднести камеру ближе;
     'steady' — держать неподвижно (progress 0…1 — сколько уже простояло);
     'fire'   — снимать (один раз);   'next' — снято, ждём следующий лист. */
export const AUTO_HOLD_MS = 900;
export const AUTO_MIN_AREA = 0.1;
const AUTO_STILL = 0.025;
const AUTO_REARM_MISSES = 3;
const AUTO_NEW_SHEET = 0.15;
const maxShift = (a, b) => Math.max(...a.map((p, i) => dist(p, b[i])));

/* Подсказка над кадром без автоснимка (режим «Вручную»): 'search' — листа
   нет, 'closer' — лист мелкий, 'found' — рамка видна, можно снимать. */
export const sheetHint = (found, w, h) => {
    if (!found || found.confidence < SHOW_CONFIDENCE) return 'search';
    return quadArea(found.corners) < AUTO_MIN_AREA * w * h ? 'closer' : 'found';
};

export const createAutoShutter = ({ hold = AUTO_HOLD_MS } = {}) => {
    let anchor = null;
    let since = 0;
    let armed = true;
    let misses = 0;
    let taken = null;
    return {
        push(found, w, h, now) {
            const diag = Math.hypot(w, h);
            if (!found || found.confidence < SHOW_CONFIDENCE) {
                misses += 1;
                anchor = null;
                if (!armed && misses >= AUTO_REARM_MISSES) { armed = true; taken = null; }
                return { state: armed ? 'search' : 'next', progress: 0 };
            }
            misses = 0;
            if (!armed) {
                if (!taken || maxShift(taken, found.corners) < AUTO_NEW_SHEET * diag) return { state: 'next', progress: 0 };
                armed = true;
                taken = null;
            }
            if (quadArea(found.corners) < AUTO_MIN_AREA * w * h) { anchor = null; return { state: 'closer', progress: 0 }; }
            if (found.confidence < AUTO_CONFIDENCE) { anchor = null; return { state: 'steady', progress: 0 }; }
            if (!anchor || maxShift(anchor, found.corners) > AUTO_STILL * diag) { anchor = found.corners; since = now; }
            const progress = Math.min(1, (now - since) / hold);
            if (progress < 1) return { state: 'steady', progress };
            armed = false;
            taken = found.corners;
            anchor = null;
            return { state: 'fire', progress: 1 };
        },
        /* Снимок сделан не автоснимком (кнопкой) — тоже ждать следующий лист. */
        took(corners) { armed = false; taken = corners; anchor = null; misses = 0; },
        reset() { anchor = null; armed = true; misses = 0; taken = null; },
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
    // Нижняя граница фона — половина бумаги страницы: тень так глубоко не
    // ложится, а без границы тёмная печать или фото шире клетки «выбеливались»
    // в серое пятно (усиление до трёх раз; разбор, 23.10.2026).
    const sorted = Array.from(lums).sort((a, b) => a - b);
    const median = sorted[sorted.length >> 1] || 255;
    const floor = Math.max(24, median * 0.5);
    for (let k = 0; k < grid.length; k += 1) {
        if (grid[k] < floor) grid[k] = floor;
    }
    return { grid, gw, gh, block, median };
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
/* Медиана «бумаги» ниже — это не лист, а тёмный кадр. */
const DARK_PAGE = 60;

/* Применить фильтр скана к странице (RGBA w×h) на месте и вернуть её же.
   color — фон белый, чернила своего цвета чуть насыщеннее; gray — как
   ксерокопия; photo — без изменений. */
export const enhancePage = (data, w, h, mode) => {
    if (mode !== 'color' && mode !== 'gray') return data;
    const { grid, gw, gh, block, median } = paperBackground(data, w, h);
    // Бумаги на снимке нет (крышка объектива, тёмная комната): деление на такой
    // «фон» вытянуло бы шум в светлую кашу — страница остаётся как снята.
    if (median < DARK_PAGE) return data;
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
