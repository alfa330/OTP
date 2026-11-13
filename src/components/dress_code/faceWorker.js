/*
 * Поиск лица в кадре камеры «Дресс-кода» — в своём потоке, чтобы видео и кнопки не
 * замирали: кадр проверяется несколько раз в секунду.
 *
 * Всё работает в браузере: кадры никуда не отправляются, наружу уходит только готовый
 * снимок. Модель — YuNet из OpenCV Zoo (MIT, YUNET-LICENSE.txt), 232 КБ, вход 320×320;
 * движок — ONNX Runtime (WebAssembly), тот же файл, что у вырезания фона «Доски почёта»
 * (events/cutout/cutoutWorker.js): браузер и воркер портала держат его в кэше, второй
 * раз он не качается.
 *
 * Сообщения: { id, type: 'init' }                     → { id, type: 'ready' }
 *            { id, type: 'detect', data: ArrayBuffer } → { id, type: 'faces', faces }
 *            при сбое                                  → { id, type: 'error', message }
 * data — RGBA кадра 320×320; faces — [{x, y, w, h, score}] в пикселях этого кадра.
 */
import * as ort from 'onnxruntime-web/wasm';
import wasmUrl from 'onnxruntime-web/ort-wasm-simd-threaded.wasm?url';
import modelUrl from './yunet-320.onnx?url';
import { FACE_INPUT, decodeFaces, toInputTensor } from './faceMath';

ort.env.wasm.wasmPaths = { wasm: wasmUrl };
// Потоки WebAssembly требуют изоляции страницы (COOP/COEP), её у портала нет —
// один поток, без попытки и предупреждения в консоли.
ort.env.wasm.numThreads = 1;

let sessionPromise = null;

const getSession = () => {
    if (!sessionPromise) {
        sessionPromise = (async () => {
            const response = await fetch(modelUrl);
            if (!response.ok) throw new Error(`Модель не загрузилась (${response.status})`);
            const model = new Uint8Array(await response.arrayBuffer());
            return ort.InferenceSession.create(model, { executionProviders: ['wasm'] });
        })().catch((error) => {
            // Не закрепляем неудачу: со следующей попытки (связь вернулась) — заново.
            sessionPromise = null;
            throw error;
        });
    }
    return sessionPromise;
};

self.onmessage = async (event) => {
    const { id, type, data } = event.data || {};
    try {
        const session = await getSession();
        if (type === 'init') {
            self.postMessage({ id, type: 'ready' });
            return;
        }
        const input = new ort.Tensor('float32', toInputTensor(new Uint8ClampedArray(data), FACE_INPUT),
            [1, 3, FACE_INPUT, FACE_INPUT]);
        const result = await session.run({ input });
        const outputs = {};
        for (const name of Object.keys(result)) outputs[name] = result[name].data;
        self.postMessage({ id, type: 'faces', faces: decodeFaces(outputs, FACE_INPUT) });
    } catch (error) {
        self.postMessage({ id, type: 'error', message: String(error?.message || error) });
    }
};
