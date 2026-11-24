/*
 * Своё распознавание штрихкода — в отдельном потоке (FaScanner.jsx): живой кадр с проходом резкости
 * считается десятки миллисекунд, а большое нечитаемое фото — секунды; в главном потоке на это время
 * замирали бы камера, «Закрыть» и поле номера.
 *
 * Сообщение: { kind: 'frame' | 'still', gray (Uint8Array, серый снимок), width, height, options }.
 * Ответ: кадр — { kind, texts: [строки кодов] }, фото — { kind, found: [{ format, text }] }.
 */
import { scanGray, scanStill } from './barcodeScan';

self.onmessage = (event) => {
    const { kind, gray, width, height, options } = event.data || {};
    if (kind === 'frame') {
        let texts = [];
        try {
            texts = scanGray(gray, width, height, options).map((found) => found.text);
        } catch {
            texts = [];
        }
        self.postMessage({ kind, texts });
        return;
    }
    let found = [];
    try {
        found = scanStill(gray, width, height);
    } catch {
        found = [];
    }
    self.postMessage({ kind: 'still', found });
};
