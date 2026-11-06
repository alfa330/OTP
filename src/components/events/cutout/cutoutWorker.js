/*
 * Нейросеть вырезания фона — в своём потоке: прогон MODNet занимает секунду-три,
 * и в главном потоке на это время замирали бы окно тренера и индикатор.
 *
 * Всё работает в браузере: фото никуда не отправляется, пока тренер не нажмёт
 * «Сохранить фото». Модель (13 МБ, MODNet fp16, Apache 2.0 — MODNET-LICENSE.txt)
 * и движок ONNX Runtime (WebAssembly) — файлы сборки с хэшем в имени: браузер и
 * воркер портала (public/sw.js) кладут их в кэш, второй раз они не качаются.
 *
 * Сообщение: { id, input (Float32Array [3, h, w]), width, height }.
 * Ответы:    { id, type: 'progress', stage: 'model', loaded, total }
 *            { id, type: 'progress', stage: 'run' }
 *            { id, type: 'result', matte (Float32Array [h, w]), width, height }
 *            { id, type: 'error', message }
 */
import * as ort from 'onnxruntime-web/wasm';
import wasmUrl from 'onnxruntime-web/ort-wasm-simd-threaded.wasm?url';
import modelUrl from './modnet-portrait-fp16.onnx?url';

ort.env.wasm.wasmPaths = { wasm: wasmUrl };
// Потоки WebAssembly требуют изоляции страницы (COOP/COEP), её у портала нет —
// один поток, без попытки и предупреждения в консоли.
ort.env.wasm.numThreads = 1;

let sessionPromise = null;

const fetchModel = async (post) => {
    const response = await fetch(modelUrl);
    if (!response.ok) throw new Error(`Модель не загрузилась (${response.status})`);
    const total = Number(response.headers.get('content-length')) || 0;
    if (!response.body || typeof response.body.getReader !== 'function') {
        return new Uint8Array(await response.arrayBuffer());
    }
    const reader = response.body.getReader();
    const chunks = [];
    let loaded = 0;
    for (;;) {
        // eslint-disable-next-line no-await-in-loop
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        loaded += value.length;
        post({ type: 'progress', stage: 'model', loaded, total });
    }
    const bytes = new Uint8Array(loaded);
    let offset = 0;
    for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.length;
    }
    return bytes;
};

const getSession = (post) => {
    if (!sessionPromise) {
        sessionPromise = (async () => {
            const model = await fetchModel(post);
            return ort.InferenceSession.create(model, { executionProviders: ['wasm'] });
        })().catch((error) => {
            // Не закрепляем неудачу: со следующей попытки (связь вернулась) — заново.
            sessionPromise = null;
            throw error;
        });
    }
    return sessionPromise;
};

/* Прогоны — строго по очереди: тренер может выбрать другое фото, пока первое
   ещё в работе, а два одновременных run() на одной сессии WebAssembly-движок
   не обещает. Окно само отбрасывает ответ устаревшего прогона. */
let queue = Promise.resolve();

const handle = async (event) => {
    const { id, input, width, height } = event.data || {};
    const post = (message) => self.postMessage({ id, ...message });
    try {
        const session = await getSession(post);
        post({ type: 'progress', stage: 'run' });
        const feeds = { [session.inputNames[0]]: new ort.Tensor('float32', input, [1, 3, height, width]) };
        const output = await session.run(feeds);
        const tensor = output[session.outputNames[0]];
        const [, , outHeight, outWidth] = tensor.dims;
        // Своя копия: буфер тензора принадлежит памяти WebAssembly, передать
        // его в главный поток без копии нельзя.
        const matte = new Float32Array(tensor.data);
        self.postMessage({ id, type: 'result', matte, width: outWidth, height: outHeight }, [matte.buffer]);
    } catch (error) {
        post({ type: 'error', message: String(error?.message || error || 'Ошибка') });
    }
};

self.onmessage = (event) => {
    queue = queue.then(() => handle(event));
};
