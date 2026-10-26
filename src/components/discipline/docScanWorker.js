/*
 * Живой поиск листа — в отдельном потоке (DocumentScanner.jsx). На телефоне
 * поиск занимает десятки миллисекунд несколько раз в секунду; в главном потоке
 * на это время замирали бы рамка в видоискателе, кнопки и жесты.
 *
 * Сообщение: { id, data (RGBA вдвое крупнее кадра поиска), width, height }.
 * Ответ: { id, found ({ corners, confidence, coverage } или null), width, height }.
 */
import { detectDocument, halveRGBA } from './docScan';

self.onmessage = (event) => {
    const { id, data, width, height } = event.data || {};
    let found = null;
    let frame = { width: 0, height: 0 };
    try {
        frame = halveRGBA(new Uint8ClampedArray(data), width, height);
        found = detectDocument(frame.data, frame.width, frame.height);
    } catch {
        found = null;
    }
    self.postMessage({ id, found, width: frame.width, height: frame.height });
};
