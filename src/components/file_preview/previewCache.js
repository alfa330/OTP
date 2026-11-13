/* Скачанные для просмотра файлы — на время жизни вкладки.

   Повторное открытие того же вложения и «Скачать» из окна просмотра не
   тянут файл с сервера второй раз. Память ограничена: старые записи
   вытесняются первыми (порядок вставки в Map = порядок использования). */

const LIMIT_BYTES = 80 * 1024 * 1024;

export function createPreviewCache(limit = LIMIT_BYTES) {
    const entries = new Map();
    let total = 0;
    return {
        get(key) {
            const blob = entries.get(key);
            if (!blob) return null;
            entries.delete(key);
            entries.set(key, blob);
            return blob;
        },
        put(key, blob) {
            if (!blob || !Number.isFinite(blob.size) || blob.size > limit) return;
            if (entries.has(key)) total -= entries.get(key).size;
            entries.delete(key);
            entries.set(key, blob);
            total += blob.size;
            for (const [oldKey, oldBlob] of entries) {
                if (total <= limit) break;
                entries.delete(oldKey);
                total -= oldBlob.size;
            }
        },
        get size() {
            return total;
        },
    };
}

export const previewCache = createPreviewCache();
