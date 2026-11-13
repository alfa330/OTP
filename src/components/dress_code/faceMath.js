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
 *   facesInFrame   лица из координат кадра сети → в доли РАМКИ на экране;
 *   frameAdvice    что не так с кадром и что сказать человеку;
 *   createHintSmoother / createAutoShutter — подсказка не мигает, снимок — когда человек
 *                  ровно стоит в рамке заданное время.
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

/* Как видимая часть видео вписана во вход сети: слева сверху, без искажения.
   → { scale, width, height } — размер картинки внутри входа side×side. */
export const fitInput = (regionWidth, regionHeight, side = FACE_INPUT) => {
    if (!(regionWidth > 0 && regionHeight > 0)) return { scale: 0, width: 0, height: 0 };
    const scale = Math.min(side / regionWidth, side / regionHeight);
    return { scale, width: Math.max(1, Math.round(regionWidth * scale)), height: Math.max(1, Math.round(regionHeight * scale)) };
};

/* Лица из пикселей входа сети → в доли РАМКИ: (0, 0) — левый верхний угол рамки,
   (1, 1) — правый нижний. `fit` — из fitInput (картинка во входе), `box` — видео на
   экране, `rect` — рамка на экране. Лево-право не переворачиваем: рамка стоит по
   центру, и подсказки про сторону нет — зеркальному превью всё равно. */
export const facesInFrame = (faces, fit, box, rect) => {
    if (!fit?.width || !fit?.height || !box?.width || !box?.height || !rect?.width || !rect?.height) return [];
    return (faces || []).map((face) => {
        const left = (face.x / fit.width) * box.width;
        const top = (face.y / fit.height) * box.height;
        const width = (face.w / fit.width) * box.width;
        const height = (face.h / fit.height) * box.height;
        return {
            x: (left - (rect.left - box.left)) / rect.width,
            y: (top - (rect.top - box.top)) / rect.height,
            w: width / rect.width,
            h: height / rect.height,
            score: face.score,
        };
    });
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
    none: 'Встаньте в рамку',
    several: 'В кадре — только вы',
    too_close: 'Держите телефон чуть дальше',
    head_cropped: 'Голова не влезает в кадр',
    off_center: 'Встаньте по центру',
    too_far: 'Подойдите ближе',
    too_low: 'Совместите голову с контуром',
    too_dark: 'Темно — встаньте к свету',
    ok: 'Отлично, не двигайтесь',
});

/* Правила кадра, в долях рамки. Прямоугольник лица у сети — от линии волос до подбородка;
   макушка выше него на headroom высоты лица.
     maxFace  лицо крупнее — в рамку не входит одежда (телефон слишком близко). Порог не
              «до пояса» строго: на вытянутой руке рамка захватывает до груди-живота, и по
              такому кадру верх одежды уже оценивается; дальше — лучше, туда ведёт силуэт;
     minFace  лицо мельче — человек далеко, одежду не разобрать;
     minTop / maxTop — где макушка: не выше края рамки и не ниже трети (иначе низ кадра пуст);
     maxOffCenter — насколько центр лица может уйти от середины рамки;
     secondFace — второе лицо считается «вторым человеком», если оно не меньше этой доли главного. */
export const FRAME_RULES = Object.freeze({
    headroom: 0.15, minTop: 0.01, maxTop: 0.3, maxFace: 0.34, minFace: 0.1, maxOffCenter: 0.14, secondFace: 0.45,
});

/* В «не двигайтесь» допуски чуть шире: человек у самой границы не должен срывать отсчёт
   дрожанием руки. */
const RELAX = Object.freeze({ maxFace: 1.08, minFace: 0.9, maxOffCenter: 1.25, top: 0.02 });

/* Что с кадром. faces — лица в долях рамки (facesInFrame).
   → { code, ok, face? }: code — ключ ADVICE_TEXT, face — главное лицо {cx, cy, h}. */
export const frameAdvice = (faces, { dark = false, relaxed = false } = {}) => {
    const list = (faces || []).filter((face) => face && face.w > 0 && face.h > 0);
    if (!list.length) return { code: 'none', ok: false };
    const main = list.reduce((best, face) => (face.h > best.h ? face : best), list[0]);
    const cx = main.x + main.w / 2;
    const cy = main.y + main.h / 2;
    const face = { cx, cy, h: main.h };
    const fail = (code) => ({ code, ok: false, face });
    // Второй человек — только тот, кто попадёт на фото: центр лица внутри рамки.
    const others = list.filter((item) => item !== main
        && item.h >= main.h * FRAME_RULES.secondFace
        && item.x + item.w / 2 > 0 && item.x + item.w / 2 < 1
        && item.y + item.h / 2 > 0 && item.y + item.h / 2 < 1);
    if (others.length) return fail('several');
    const maxFace = FRAME_RULES.maxFace * (relaxed ? RELAX.maxFace : 1);
    const minFace = FRAME_RULES.minFace * (relaxed ? RELAX.minFace : 1);
    const maxOff = FRAME_RULES.maxOffCenter * (relaxed ? RELAX.maxOffCenter : 1);
    const slack = relaxed ? RELAX.top : 0;
    const top = main.y - main.h * FRAME_RULES.headroom;
    // «Дальше» раньше «голова не влезает»: вплотную к камере голова не влезает всегда,
    // и отодвинуть телефон чинит оба.
    if (main.h > maxFace) return fail('too_close');
    if (top < FRAME_RULES.minTop - slack) return fail('head_cropped');
    if (main.x < -slack || main.x + main.w > 1 + slack || Math.abs(cx - 0.5) > maxOff) return fail('off_center');
    if (main.h < minFace) return fail('too_far');
    if (top > FRAME_RULES.maxTop + slack) return fail('too_low');
    if (dark) return fail('too_dark');
    return { code: 'ok', ok: true, face };
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

/* Автоснимок: человек стоит в рамке `holdMs` подряд и не двигается (центр лица сместился
   не больше maxShift рамки, размер — не больше maxScale). Сдвинулся — отсчёт заново; кадр
   перестал быть хорошим — сброс, но не сразу: одна сорвавшаяся проверка (нейросеть моргнула,
   дрогнула рука) в пределах graceMs отсчёт не обнуляет. update → { progress: 0..1, fire }. */
export const createAutoShutter = ({ holdMs = 1200, maxShift = 0.04, maxScale = 0.15, graceMs = 320 } = {}) => {
    let since = null;
    let anchor = null;
    let lastOkAt = 0;
    const reset = () => { since = null; anchor = null; };
    return {
        update(advice, now) {
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
