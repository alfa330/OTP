/* Временный кэш просмотра вложений: сами файлы и распознанный текст.
 *
 * Зачем. Просмотрщик на каждое открытие заново качал файл через сервер, а
 * распознанный ИИ текст жил только пока окно открыто: закрыл документ, открыл
 * снова — опять загрузка и опять распознавание. Верификатор за разбор одного
 * водителя возвращается к одному и тому же фото по нескольку раз.
 *
 * Где лежит. Только в памяти вкладки — ни на диск, ни в хранилище браузера
 * ничего не пишется: это документы водителей, и после закрытия вкладки от них
 * не должно оставаться следа. Отсюда же срок жизни записи и сброс при смене
 * человека в портале (setOwner).
 *
 * Модуль без React и без сети — его гоняет node-тест. */

const MINUTE = 60 * 1000;

export const ATTACHMENT_CACHE_LIMITS = Object.freeze({
    // Файл держим, пока им пользуются: час с последнего открытия.
    mediaTtlMs: 60 * MINUTE,
    // В сумме не больше, чем разумно отдать вкладке; один файл — до 20 МБ.
    mediaMaxBytes: 160 * 1024 * 1024,
    mediaMaxItems: 60,
    // Текст маленький, а распознавание платное — его хватает на всю смену.
    textTtlMs: 12 * 60 * MINUTE,
    textMaxItems: 400,
});

/* Список «недавно использованных» на Map: порядок вставки и есть порядок
   давности, обращение переставляет запись в конец. */
function createLru({ ttlMs, maxItems, maxWeight = Infinity, weigh = () => 0, now }) {
    const items = new Map();
    let weight = 0;
    const remove = (key) => {
        const entry = items.get(key);
        if (!entry) return;
        weight -= entry.weight;
        items.delete(key);
    };
    return {
        get(key) {
            const entry = items.get(key);
            if (!entry) return undefined;
            if (now() - entry.at > ttlMs) { remove(key); return undefined; }
            // Обращение продлевает жизнь: файл, к которому возвращаются, остаётся.
            items.delete(key);
            entry.at = now();
            items.set(key, entry);
            return entry.value;
        },
        set(key, value) {
            remove(key);
            const size = weigh(value);
            // Запись больше всего кэша не кладём вовсе — она вытеснила бы всё и не поместилась.
            if (size > maxWeight) return;
            items.set(key, { value, weight: size, at: now() });
            weight += size;
            for (const oldest of items.keys()) {
                if (items.size <= maxItems && weight <= maxWeight) break;
                remove(oldest);
            }
        },
        /* Есть ли запись — без продления её жизни: так спрашивает отрисовка,
           которая может выполниться несколько раз на одно открытие. */
        peek(key) {
            const entry = items.get(key);
            return entry && now() - entry.at <= ttlMs ? entry.value : undefined;
        },
        delete: remove,
        clear() { items.clear(); weight = 0; },
        get size() { return items.size; },
        get weight() { return weight; },
    };
}

export function createAttachmentCache(limits = {}, now = () => Date.now()) {
    const config = { ...ATTACHMENT_CACHE_LIMITS, ...limits };
    const media = createLru({
        ttlMs: config.mediaTtlMs, maxItems: config.mediaMaxItems, maxWeight: config.mediaMaxBytes,
        weigh: (blob) => Number(blob?.size) || 0, now,
    });
    const text = createLru({ ttlMs: config.textTtlMs, maxItems: config.textMaxItems, now });
    let owner;
    const textKey = (key, page) => `${key}\u0000${page}`;
    return {
        getMedia: (key) => media.get(key) || null,
        peekMedia: (key) => media.peek(key) || null,
        putMedia: (key, blob) => { if (blob?.size) media.set(key, blob); },
        getText: (key, page) => text.get(textKey(key, page)) || null,
        putText: (key, page, value) => { text.set(textKey(key, page), value); },
        dropText: (key, page) => { text.delete(textKey(key, page)); },
        clear() { media.clear(); text.clear(); },
        /* Кэш принадлежит одному человеку: вошёл другой в ту же вкладку — всё
           накопленное сбрасывается, чужих документов он не унаследует. */
        setOwner(nextOwner) {
            const value = nextOwner == null ? '' : String(nextOwner);
            if (owner !== undefined && owner !== value) { media.clear(); text.clear(); }
            owner = value;
        },
        stats: () => ({ mediaItems: media.size, mediaBytes: media.weight, textItems: text.size }),
    };
}

// Один на вкладку: просмотрщик размонтируется при закрытии, кэш его переживает.
export const attachmentCache = createAttachmentCache();
