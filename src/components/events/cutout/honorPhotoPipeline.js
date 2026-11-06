/*
 * Фото для «Доски почёта» в окне тренера: снимок с телефона → два варианта для
 * карточки — «без фона» (человек на прозрачном фоне, как на образце с цветными
 * плитками) и «с фоном» (то же фото, уменьшенное). Тренер выбирает, сервер
 * получает только выбранный вариант (honor_board/routes.py: /photos) и
 * пересобирает его ещё раз сам.
 *
 * Фон вырезает нейросеть MODNet в отдельном потоке (cutoutWorker.js); здесь —
 * всё, что требует DOM: разбор файла, холсты, маска, обрезка и сжатие.
 */
import {
    alphaBounds, coverage, cropAround, fitSize, matteToRGBA, modelInputSize, toModelInput,
} from './cutoutMath';

// Длинная сторона снимка, с которым работаем: больше доске не нужно, а фото
// с телефона в 12 Мп — это 48 МБ точек на каждый холст.
const WORK_SIDE = 1600;
// Человек занимает меньше 2 % кадра — маска почти пустая: нейросеть никого не
// нашла (пейзаж, документ), и «вырезанный» снимок был бы пустой картинкой.
const MIN_COVERAGE = 0.02;

export class HonorPhotoError extends Error {}

let worker = null;
let nextId = 0;
const pending = new Map();

const dropWorker = (message) => {
    if (worker) {
        worker.onmessage = null;
        worker.onerror = null;
        worker.terminate();
    }
    worker = null;
    for (const task of pending.values()) task.reject(new HonorPhotoError(message));
    pending.clear();
};

/** Выгрузить нейросеть: окно тренера закрыли. Незавершённые задачи получают
    отказ — их ответ всё равно уже никто не ждёт. */
export const releaseCutout = () => {
    if (worker) dropWorker('Окно закрыто');
};

const getWorker = () => {
    if (worker) return worker;
    if (typeof Worker !== 'function') throw new HonorPhotoError('Этот браузер не умеет убирать фон');
    worker = new Worker(new URL('./cutoutWorker.js', import.meta.url), { type: 'module' });
    worker.onmessage = (event) => {
        const data = event.data || {};
        const task = pending.get(data.id);
        if (!task) return;
        if (data.type === 'progress') {
            task.onProgress?.(data);
        } else if (data.type === 'result') {
            pending.delete(data.id);
            task.resolve(data);
        } else if (data.type === 'error') {
            pending.delete(data.id);
            task.reject(new HonorPhotoError(data.message || 'Не удалось убрать фон'));
        }
    };
    worker.onerror = () => dropWorker('Не удалось убрать фон на этом устройстве');
    return worker;
};

const runModel = (input, width, height, onProgress) => new Promise((resolve, reject) => {
    let target;
    try {
        target = getWorker();
    } catch (error) {
        reject(error);
        return;
    }
    nextId += 1;
    const id = nextId;
    pending.set(id, { resolve, reject, onProgress });
    target.postMessage({ id, input, width, height }, [input.buffer]);
});

const canvasOf = (width, height) => {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    return canvas;
};

const loadWithImg = (file) => new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
        URL.revokeObjectURL(url);
        resolve(image);
    };
    image.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new HonorPhotoError('Не удалось открыть фото — подойдёт JPG, PNG или WEBP'));
    };
    image.src = url;
});

/** Файл → холст снимка (повёрнут по EXIF, длинная сторона ≤ WORK_SIDE). */
export const decodePhoto = async (file) => {
    if (!file || !String(file.type || '').startsWith('image/')) {
        throw new HonorPhotoError('Выберите фото — JPG, PNG или WEBP');
    }
    let source = null;
    try {
        source = await createImageBitmap(file, { imageOrientation: 'from-image' });
    } catch {
        // Старый Safari без imageOrientation или HEIC, который умеет только <img>.
        source = await loadWithImg(file);
    }
    const width = source.width || source.naturalWidth;
    const height = source.height || source.naturalHeight;
    if (!width || !height) throw new HonorPhotoError('Не удалось открыть фото');
    const size = fitSize(width, height, WORK_SIDE);
    const canvas = canvasOf(size.width, size.height);
    const context = canvas.getContext('2d');
    context.imageSmoothingQuality = 'high';
    context.drawImage(source, 0, 0, size.width, size.height);
    if (typeof source.close === 'function') source.close();
    return canvas;
};

const toBlob = (canvas, type, quality) => new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), type, quality);
});

/* Сжатие для загрузки. WEBP меньше и держит прозрачность; Safari до 17 его не
   кодирует и молча отдаёт PNG — тогда PNG (прозрачность нужна) или JPEG. */
const encode = async (canvas, transparent) => {
    const webp = await toBlob(canvas, 'image/webp', 0.92);
    if (webp && webp.type === 'image/webp') return webp;
    return transparent ? toBlob(canvas, 'image/png') : toBlob(canvas, 'image/jpeg', 0.9);
};

/** Вариант «с фоном»: снимок как есть, сжатый. Прозрачное в нём (PNG, где фон
    уже вырезали в другой программе) — белым: превью в окне показывает ровно
    то, что сохранит сервер. */
export const photoAsIs = async (canvas) => {
    const flat = canvasOf(canvas.width, canvas.height);
    const context = flat.getContext('2d');
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, flat.width, flat.height);
    context.drawImage(canvas, 0, 0);
    const blob = await encode(flat, false);
    if (!blob) throw new HonorPhotoError('Не удалось подготовить фото');
    return { blob, width: flat.width, height: flat.height, cutout: false };
};

/** Вариант «без фона»: человек на прозрачном фоне, обрезанный по фигуре.
    onProgress({stage: 'model', loaded, total} | {stage: 'run'}). */
export const photoWithoutBackground = async (canvas, { onProgress } = {}) => {
    const { width, height } = canvas;
    const model = modelInputSize(width, height);
    const small = canvasOf(model.width, model.height);
    const smallContext = small.getContext('2d', { willReadFrequently: true });
    smallContext.imageSmoothingQuality = 'high';
    smallContext.drawImage(canvas, 0, 0, model.width, model.height);
    const input = toModelInput(smallContext.getImageData(0, 0, model.width, model.height).data, model.width, model.height);

    const result = await runModel(input, model.width, model.height, onProgress);

    // Маска модели — в альфа-канал холста её размера, затем растягиваем её до
    // снимка самим браузером (сглаженно) и вырезаем: destination-in оставляет
    // от снимка только то, что под непрозрачной маской.
    const mask = canvasOf(result.width, result.height);
    mask.getContext('2d').putImageData(
        new ImageData(matteToRGBA(result.matte, result.width, result.height), result.width, result.height), 0, 0);
    const cut = canvasOf(width, height);
    const cutContext = cut.getContext('2d', { willReadFrequently: true });
    cutContext.drawImage(canvas, 0, 0);
    cutContext.globalCompositeOperation = 'destination-in';
    cutContext.imageSmoothingQuality = 'high';
    cutContext.drawImage(mask, 0, 0, width, height);
    cutContext.globalCompositeOperation = 'source-over';

    const pixels = cutContext.getImageData(0, 0, width, height).data;
    if (coverage(pixels, width, height) < MIN_COVERAGE) {
        throw new HonorPhotoError('На фото не нашлось человека — выберите портрет или оставьте фото с фоном');
    }
    const box = cropAround(alphaBounds(pixels, width, height), width, height);
    const out = canvasOf(box.width, box.height);
    out.getContext('2d').drawImage(cut, box.left, box.top, box.width, box.height, 0, 0, box.width, box.height);
    const blob = await encode(out, true);
    if (!blob) throw new HonorPhotoError('Не удалось подготовить фото');
    return { blob, width: out.width, height: out.height, cutout: true };
};
