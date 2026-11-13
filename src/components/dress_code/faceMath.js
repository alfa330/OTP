/* «Дресс-код» (#418): расчёты автопроверки кадра — без React и без сети, под node-тест
 * (tests/dress_code_face.test.mjs).
 *
 * Владелец 13.11.2026: «сделать авто проверку кадра на человека: когда он встаёт на
 * идеальный кадр, камера автоматически делает снимок и отправляет его». Лицо в кадре
 * находит маленькая нейросеть прямо в телефоне (YuNet, faceWorker.js) — кадры никуда не
 * уходят, пока снимок не сделан. Здесь — всё, что вокруг неё:
 *
 *   toInputTensor  кадр RGBA → вход сети;
 *   decodeFaces    ответ сети → прямоугольники лиц;
 *   facesInFrame   лица из координат входа сети → в доли КАДРА (того, что попадёт на фото);
 *   frameAdvice    что не так с кадром и что сказать человеку;
 *   silhouetteGuide где на экране нарисовать контур головы;
 *   createHintSmoother / createAutoShutter — подсказка не мигает, снимок — когда человек
 *                  ровно стоит в кадре заданное время.
 *
 * Владелец 13.11.2026 (вечер): «авто фотографирование сработало даже без условия по пояс…
 * добавь суперадмину тестовый стенд, чтобы я проверял и корректировал». Поэтому главное
 * правило — ПОЯС ДОЛЖЕН ПОМЕЩАТЬСЯ В КАДР: пояс оценивается по лицу (на `waist` высот лица
 * ниже линии волос), а сами числа правил приходят с сервера и правятся в стенде камеры.
 */

export const FACE_INPUT = 320;
export const FACE_SCORE = 0.6;
const STRIDES = [8, 16, 32];
const NMS_IOU = 0.3;
const MAX_FACES = 8;

/* RGBA (side×side) → Float32Array [3, side, side]: порядок каналов B, G, R и значения
   0..255 без нормировки — так обучена YuNet (порядок OpenCV). */
export const toInputTensor = (rgba, side = FACE_INPUT) => {
    const plane = side * side;
    const out = new Float32Array(3 * plane);
    for (let i = 0, p = 0; p < plane; i += 4, p += 1) {
        out[p] = rgba[i + 2];
        out[plane + p] = rgba[i + 1];
        out[2 * plane + p] = rgba[i];
    }
    return out;
};

const iou = (a, b) => {
    const x1 = Math.max(a.x, b.x);
    const y1 = Math.max(a.y, b.y);
    const x2 = Math.min(a.x + a.w, b.x + b.w);
    const y2 = Math.min(a.y + a.h, b.y + b.h);
    const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
    return inter / (a.w * a.h + b.w * b.h - inter + 1e-9);
};

/* Ответ YuNet → лица [{x, y, w, h, score}] в пикселях входа сети, уверенные первыми.
   Сеть отвечает по трём сеткам (шаг 8, 16, 32): в каждой клетке — уверенность (cls × obj)
   и прямоугольник относительно клетки. Разбор — как в OpenCV FaceDetectorYN; сверен с
   onnxruntime на тестовом снимке (лицо найдено там же, где его находит OpenCV-разбор). */
export const decodeFaces = (outputs, side = FACE_INPUT, threshold = FACE_SCORE) => {
    const faces = [];
    for (const stride of STRIDES) {
        const cols = Math.floor(side / stride);
        const cells = cols * cols;
        const cls = outputs?.[`cls_${stride}`];
        const obj = outputs?.[`obj_${stride}`];
        const bbox = outputs?.[`bbox_${stride}`];
        if (!cls || !obj || !bbox || cls.length < cells || bbox.length < cells * 4) continue;
        for (let idx = 0; idx < cells; idx += 1) {
            const score = Math.sqrt(Math.min(1, Math.max(0, cls[idx])) * Math.min(1, Math.max(0, obj[idx])));
            if (!(score >= threshold)) continue;
            const row = Math.floor(idx / cols);
            const col = idx % cols;
            const cx = (col + bbox[idx * 4]) * stride;
            const cy = (row + bbox[idx * 4 + 1]) * stride;
            const w = Math.exp(bbox[idx * 4 + 2]) * stride;
            const h = Math.exp(bbox[idx * 4 + 3]) * stride;
            if (!(w > 0 && h > 0 && Number.isFinite(w) && Number.isFinite(h))) continue;
            faces.push({ x: cx - w / 2, y: cy - h / 2, w, h, score });
        }
    }
    faces.sort((a, b) => b.score - a.score);
    const kept = [];
    for (const face of faces) {
        if (kept.every((other) => iou(face, other) < NMS_IOU)) kept.push(face);
        if (kept.length >= MAX_FACES) break;
    }
    return kept;
};

/* Как снимаемая часть кадра камеры вписана во вход сети: слева сверху, без искажения.
   → { scale, width, height } — размер картинки внутри входа side×side. */
export const fitInput = (regionWidth, regionHeight, side = FACE_INPUT) => {
    if (!(regionWidth > 0 && regionHeight > 0)) return { scale: 0, width: 0, height: 0 };
    const scale = Math.min(side / regionWidth, side / regionHeight);
    return { scale, width: Math.max(1, Math.round(regionWidth * scale)), height: Math.max(1, Math.round(regionHeight * scale)) };
};

/* Лица из пикселей входа сети → в доли КАДРА: (0, 0) — левый верхний угол того, что
   попадёт на фото, (1, 1) — правый нижний. `fit` — из fitInput: во вход сети вписана
   ровно снимаемая часть кадра камеры. Лево-право не переворачиваем: подсказки про
   сторону нет — зеркальному превью всё равно. */
export const facesInFrame = (faces, fit) => {
    if (!fit?.width || !fit?.height) return [];
    return (faces || []).map((face) => ({
        x: face.x / fit.width,
        y: face.y / fit.height,
        w: face.w / fit.width,
        h: face.h / fit.height,
        score: face.score,
    }));
};

/* Средняя яркость части входа сети (прямоугольник в пикселях входа), каждый третий
   пиксель: темноту видно и по выборке, а считать весь кадр шесть раз в секунду незачем. */
export const regionLuma = (rgba, side, x0, y0, x1, y1, step = 3) => {
    const left = Math.max(0, Math.floor(x0));
    const top = Math.max(0, Math.floor(y0));
    const right = Math.min(side, Math.ceil(x1));
    const bottom = Math.min(side, Math.ceil(y1));
    let sum = 0;
    let count = 0;
    for (let y = top; y < bottom; y += step) {
        for (let x = left; x < right; x += step) {
            const i = (y * side + x) * 4;
            sum += 0.2126 * rgba[i] + 0.7152 * rgba[i + 1] + 0.0722 * rgba[i + 2];
            count += 1;
        }
    }
    return count ? sum / count : 0;
};

/* Короткие подсказки (владелец: «более короткими словами… держите кадр чуть дальше,
   голова не влезает в кадр»). Про сторону («левее/правее») подсказок нет намеренно:
   превью зеркальное, и «левее» каждый понял бы по-своему. */
export const ADVICE_TEXT = Object.freeze({
    none: 'Встаньте в кадр',
    several: 'В кадре — только вы',
    too_close: 'Держите телефон чуть дальше',
    head_cropped: 'Голова не влезает в кадр',
    off_center: 'Встаньте по центру',
    too_far: 'Подойдите ближе',
    too_low: 'Совместите голову с контуром',
    too_dark: 'Темно — встаньте к свету',
    ok: 'Отлично, не двигайтесь',
});

/* Правила автопроверки кадра, в долях кадра. Их правит суперадмин в стенде камеры (вкладка
   «Дресс-код» в «Отметках»), к камере сотрудника они приходят с сервера вместе с /me.
   Значения и пределы — зеркало dress_code/__init__.py (CAMERA_DEFAULTS, CAMERA_LIMITS),
   сверяет тест.
     waist           пояс: на сколько высот лица он ниже линии волос. Пояс должен помещаться
                     в кадр — это и есть «фото по пояс»; больше число — стоять дальше;
     min_face        лицо мельче этой доли высоты кадра — человек далеко, одежду не разобрать;
     max_top         макушка не ниже этой доли высоты кадра (иначе над головой пусто);
     max_off_center  насколько центр лица может уйти от середины кадра (доля ширины);
     hold_ms         сколько стоять неподвижно до автоснимка. */
export const DEFAULT_RULES = Object.freeze({ waist: 3.2, min_face: 0.1, max_top: 0.25, max_off_center: 0.14, hold_ms: 1200 });
export const RULE_LIMITS = Object.freeze({
    waist: [1.5, 5], min_face: [0.04, 0.15], max_top: [0.05, 0.6], max_off_center: [0.05, 0.4], hold_ms: [400, 5000],
});

/* Правила с сервера или из ползунков стенда → полный набор в пределах RULE_LIMITS; чего
   нет или что не число — значение по умолчанию. То же делает сервер (service.clean_camera). */
export const cleanRules = (raw) => {
    const out = { ...DEFAULT_RULES };
    if (!raw || typeof raw !== 'object') return out;
    for (const [key, [low, high]] of Object.entries(RULE_LIMITS)) {
        const value = raw[key];
        if (typeof value !== 'number' || !Number.isFinite(value)) continue;
        const clamped = Math.min(high, Math.max(low, value));
        out[key] = key === 'hold_ms' ? Math.round(clamped) : Math.round(clamped * 1000) / 1000;
    }
    return out;
};

/* Что не настраивается. Прямоугольник лица у сети — от линии волос до подбородка; макушка
   выше него на headroom высоты лица. minTop — макушка не выше края кадра. secondFace —
   второе лицо считается «вторым человеком», если оно не меньше этой доли главного. */
export const FRAME_FIXED = Object.freeze({ headroom: 0.15, minTop: 0.01, secondFace: 0.45 });

/* В «не двигайтесь» допуски чуть шире: человек у самой границы не должен срывать отсчёт
   дрожанием руки. Пояс — на 3 % высоты кадра (пара сантиметров на вытянутой руке). */
const RELAX = Object.freeze({ waist: 0.03, minFace: 0.9, maxOffCenter: 1.25, top: 0.02 });

/* Самое крупное лицо, при котором пояс ещё помещается в кадр (макушка у самого верха). */
export const largestFace = (rules = DEFAULT_RULES) => (1 - FRAME_FIXED.minTop) / (FRAME_FIXED.headroom + rules.waist);

/* Что с кадром. faces — лица в долях кадра (facesInFrame).
   → { code, ok, face?, metrics?, box? }: code — ключ ADVICE_TEXT; face — главное лицо
   {cx, cy, h} для автоснимка; metrics — измеренное для стенда: face (высота лица), top
   (макушка), waist (пояс: 1 — нижний край кадра, больше — пояс не поместился), center
   (смещение лица от середины); box — прямоугольник главного лица. */
export const frameAdvice = (faces, { dark = false, relaxed = false, rules = DEFAULT_RULES } = {}) => {
    const list = (faces || []).filter((face) => face && face.w > 0 && face.h > 0);
    if (!list.length) return { code: 'none', ok: false };
    const main = list.reduce((best, face) => (face.h > best.h ? face : best), list[0]);
    const cx = main.x + main.w / 2;
    const cy = main.y + main.h / 2;
    const top = main.y - main.h * FRAME_FIXED.headroom;
    const waist = main.y + main.h * rules.waist;
    const face = { cx, cy, h: main.h };
    const metrics = { face: main.h, top, waist, center: cx - 0.5 };
    const box = { x: main.x, y: main.y, w: main.w, h: main.h };
    const fail = (code) => ({ code, ok: false, face, metrics, box });
    // Второй человек — только тот, кто попадёт на фото: центр лица внутри кадра.
    const others = list.filter((item) => item !== main
        && item.h >= main.h * FRAME_FIXED.secondFace
        && item.x + item.w / 2 > 0 && item.x + item.w / 2 < 1
        && item.y + item.h / 2 > 0 && item.y + item.h / 2 < 1);
    if (others.length) return fail('several');
    const waistSlack = relaxed ? RELAX.waist : 0;
    const minFace = rules.min_face * (relaxed ? RELAX.minFace : 1);
    const maxOff = rules.max_off_center * (relaxed ? RELAX.maxOffCenter : 1);
    const slack = relaxed ? RELAX.top : 0;
    // Лицо такое крупное, что пояс не поместится, даже если поднять голову к самому верху:
    // телефон близко. Раньше «голова не влезает»: вплотную к камере голова не влезает
    // всегда, и отодвинуть телефон чинит оба.
    if (FRAME_FIXED.minTop + main.h * (FRAME_FIXED.headroom + rules.waist) > 1 + waistSlack) return fail('too_close');
    if (top < FRAME_FIXED.minTop - slack) return fail('head_cropped');
    if (main.x < -slack || main.x + main.w > 1 + slack || Math.abs(cx - 0.5) > maxOff) return fail('off_center');
    if (main.h < minFace) return fail('too_far');
    // Голова низко: пояс ушёл за нижний край кадра или над головой пусто — голову к контуру.
    if (waist > 1 + waistSlack || top > rules.max_top + slack) return fail('too_low');
    if (dark) return fail('too_dark');
    return { code: 'ok', ok: true, face, metrics, box };
};

/* Какие измерения укладываются в правила — для стенда: зелёное или красное у каждого числа.
   Без допусков «не двигайтесь»: стенд показывает сами правила. */
export const metricChecks = (metrics, rules = DEFAULT_RULES) => {
    if (!metrics) return { face: false, top: false, waist: false, center: false };
    return {
        face: metrics.face >= rules.min_face && metrics.face <= largestFace(rules),
        top: metrics.top >= FRAME_FIXED.minTop && metrics.top <= rules.max_top,
        waist: metrics.waist <= 1,
        center: Math.abs(metrics.center) <= rules.max_off_center,
    };
};

/* Контур-ориентир, в долях высоты кадра: где держать голову, чтобы кадр прошёл с запасом.
   Макушка — у верха; лицо — 80 % от самого крупного, при котором пояс ещё в кадре (на
   вытянутой руке лицо примерно такое и есть). → { crown, chin, face, waist }. */
export const silhouetteGuide = (rules = DEFAULT_RULES) => {
    const crown = 0.04;
    const face = Math.max(rules.min_face, largestFace(rules) * 0.8);
    const head = face * (1 + FRAME_FIXED.headroom) * 1.04;
    return {
        crown,
        chin: crown + head,
        face,
        waist: Math.min(1, crown + face * (FRAME_FIXED.headroom + rules.waist)),
    };
};

/* Подсказка не мигает: новая появляется, когда держится `need` проверок подряд. «Отлично»
   и первая подсказка — сразу. */
export const createHintSmoother = (need = 2) => {
    let shown = null;
    let candidate = null;
    let streak = 0;
    return {
        push(code) {
            if (shown === null || code === shown) {
                shown = code;
                candidate = null;
                streak = 0;
                return shown;
            }
            if (code === candidate) streak += 1;
            else { candidate = code; streak = 1; }
            if (streak >= need || code === 'ok') {
                shown = code;
                candidate = null;
                streak = 0;
            }
            return shown;
        },
        reset() { shown = null; candidate = null; streak = 0; },
    };
};

/* Автоснимок: человек стоит в кадре `holdMs` подряд и не двигается (центр лица сместился
   не больше maxShift кадра, размер — не больше maxScale). Сдвинулся — отсчёт заново; кадр
   перестал быть хорошим — сброс, но не сразу: одна сорвавшаяся проверка (нейросеть моргнула,
   дрогнула рука) в пределах graceMs отсчёт не обнуляет. update → { progress: 0..1, fire }.
   Третий аргумент update — выдержка на этот раз: стенд меняет её ползунком на ходу. */
export const createAutoShutter = ({ holdMs: defaultHold = DEFAULT_RULES.hold_ms, maxShift = 0.04, maxScale = 0.15, graceMs = 320 } = {}) => {
    let since = null;
    let anchor = null;
    let lastOkAt = 0;
    const reset = () => { since = null; anchor = null; };
    return {
        update(advice, now, hold = defaultHold) {
            const holdMs = hold > 0 ? hold : defaultHold;
            if (!advice?.ok || !advice.face) {
                if (since !== null && now - lastOkAt <= graceMs) {
                    return { progress: Math.min(1, Math.max(0, (lastOkAt - since) / holdMs)), fire: false };
                }
                reset();
                return { progress: 0, fire: false };
            }
            const { cx, cy, h } = advice.face;
            const moved = anchor && (Math.abs(cx - anchor.cx) > maxShift || Math.abs(cy - anchor.cy) > maxShift
                || Math.abs(h - anchor.h) / anchor.h > maxScale);
            if (since === null || moved) {
                since = now;
                anchor = { cx, cy, h };
            }
            lastOkAt = now;
            const progress = Math.min(1, Math.max(0, (now - since) / holdMs));
            return { progress, fire: progress >= 1 };
        },
        holding() { return since !== null; },
        reset,
    };
};
