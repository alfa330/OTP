/* Обёртка над воркером поиска лица (faceWorker.js): обещания вместо сообщений.
 *
 * Воркер создаётся при первом обращении и живёт, пока открыта камера. Любой сбой
 * (старый телефон без WebAssembly SIMD, не скачалась модель, упал поток) отдаётся
 * отказом обещания: камера тогда просто работает без автоснимка, как обычная. */

export const createFaceDetector = () => {
    let worker = null;
    let seq = 0;
    const waiting = new Map();

    const failAll = (error) => {
        for (const { reject } of waiting.values()) reject(error);
        waiting.clear();
    };

    const ensure = () => {
        if (worker) return worker;
        worker = new Worker(new URL('./faceWorker.js', import.meta.url), { type: 'module' });
        worker.onmessage = (event) => {
            const { id, type, faces, message } = event.data || {};
            const pending = waiting.get(id);
            if (!pending) return;
            waiting.delete(id);
            if (type === 'error') pending.reject(new Error(message || 'Поиск лица не сработал'));
            else pending.resolve(type === 'faces' ? (faces || []) : true);
        };
        worker.onerror = (event) => {
            event.preventDefault?.();
            failAll(new Error(event?.message || 'Поток поиска лица остановился'));
        };
        return worker;
    };

    const call = (message, transfer = []) => new Promise((resolve, reject) => {
        let target;
        try {
            target = ensure();
        } catch (error) {
            reject(error);
            return;
        }
        seq += 1;
        waiting.set(seq, { resolve, reject });
        target.postMessage({ ...message, id: seq }, transfer);
    });

    return {
        /* Загрузить движок и модель. Обещание — «готов к работе». */
        init: () => call({ type: 'init' }),
        /* Лица в кадре: buffer — RGBA 320×320 (передаётся воркеру без копирования). */
        detect: (buffer) => call({ type: 'detect', data: buffer }, [buffer]),
        dispose() {
            if (worker) worker.terminate();
            worker = null;
            failAll(new Error('Камера закрыта'));
        },
    };
};

/* Один детектор на раздел. Нейросеть начинает грузиться, как только сотрудник открыл
   «Дресс-код» (warmFaceDetector), а не когда открыл камеру: движок на мобильном интернете
   качается несколько секунд, и к первому кадру автопроверка уже должна работать. Камера
   между открытиями его не выгружает; раздел закрыли — releaseFaceDetector. */
let shared = null;

export const sharedFaceDetector = () => {
    if (!shared) shared = createFaceDetector();
    return shared;
};

export const warmFaceDetector = () => {
    sharedFaceDetector().init().catch(() => {});
};

export const releaseFaceDetector = () => {
    if (shared) shared.dispose();
    shared = null;
};
