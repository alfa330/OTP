import { useSyncExternalStore } from 'react';

/* Черновики «Чатов ОП» — личные: черновик видит только тот оператор, который
 * его набрал (решение владельца 09.10.2026). Набрал и ушёл в другой чат — в
 * списке у прежнего чата «Черновик: …», вернулся — текст в поле.
 *
 * Живут в localStorage этого браузера под учёткой человека: переживают
 * перезагрузку и соседние вкладки, а другой человек, вошедший в тот же
 * браузер, их не видит. На сервер черновики не уходят вовсе — ни записи в базу
 * на каждую паузу в наборе, ни чужих глаз. Старше недели — убираются. */

const PREFIX = 'icore.wazzup.draft.';
export const DRAFT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const EMPTY = Object.freeze({});

let state = { owner: null, drafts: EMPTY };
const listeners = new Set();

const storage = () => {
    try { return globalThis.localStorage || null; } catch { return null; }
};
const prefixOf = (owner) => `${PREFIX}${owner}.`;
export const chatDraftStorageKey = (owner, chatKey) => `${prefixOf(owner)}${chatKey}`;

const parse = (raw, now) => {
    try {
        const entry = JSON.parse(raw || 'null');
        if (!entry || typeof entry.text !== 'string') return null;
        const savedAt = Date.parse(entry.updatedAt);
        return Number.isFinite(savedAt) && now - savedAt <= DRAFT_MAX_AGE_MS ? entry : null;
    } catch {
        return null;
    }
};

function load(owner) {
    const store = storage();
    if (!store || owner == null) return EMPTY;
    const prefix = prefixOf(owner);
    const now = Date.now();
    const drafts = {};
    const stale = [];
    try {
        for (let index = 0; index < store.length; index += 1) {
            const key = store.key(index);
            if (!key?.startsWith(prefix)) continue;
            const entry = parse(store.getItem(key), now);
            if (entry) drafts[key.slice(prefix.length)] = entry;
            else stale.push(key);
        }
        stale.forEach((key) => store.removeItem(key));
    } catch { /* Хранилище недоступно — черновики живут, пока открыт чат. */ }
    return Object.freeze(drafts);
}

function ensure(owner) {
    if (owner == null) return EMPTY;
    if (state.owner !== owner) state = { owner, drafts: load(owner) };
    return state.drafts;
}

const emit = () => listeners.forEach((listener) => listener());

// Соседняя вкладка того же человека изменила черновик — список здесь видит это сразу.
let storageListening = false;
const onStorage = (event) => {
    if (state.owner == null || (event.key != null && !event.key.startsWith(prefixOf(state.owner)))) return;
    state = { owner: state.owner, drafts: load(state.owner) };
    emit();
};

export function subscribeChatDrafts(listener) {
    listeners.add(listener);
    if (!storageListening && typeof globalThis.addEventListener === 'function') {
        globalThis.addEventListener('storage', onStorage);
        storageListening = true;
    }
    return () => listeners.delete(listener);
}

export function readChatDraft(owner, chatKey) {
    if (owner == null || !chatKey) return null;
    return ensure(owner)[chatKey] || null;
}

/* entry — состояние поля: текст, шаблон, ответ, файл. Пустое — черновик снят.
   notify: false — поле пишет на каждую букву, а черновик открытого чата в списке
   не показывается: незачем перерисовывать раздел на каждое нажатие. Список
   узнаёт о черновике, когда человек уходит из чата (notifyChatDrafts). */
export function writeChatDraft(owner, chatKey, entry, { notify = true } = {}) {
    if (owner == null || !chatKey) return;
    const drafts = { ...ensure(owner) };
    const store = storage();
    const key = chatDraftStorageKey(owner, chatKey);
    if (entry && (entry.text || entry.preview || entry.attachment)) {
        const value = { ...entry, updatedAt: new Date().toISOString() };
        drafts[chatKey] = value;
        try { store?.setItem(key, JSON.stringify(value)); } catch { /* Только в памяти вкладки. */ }
    } else {
        if (!drafts[chatKey]) return;
        delete drafts[chatKey];
        try { store?.removeItem(key); } catch { /* Только в памяти вкладки. */ }
    }
    state = { owner, drafts: Object.freeze(drafts) };
    if (notify) emit();
}

export const notifyChatDrafts = emit;

// Что показать в списке чатов: текст, человеческий текст шаблона или имя файла.
export const draftPreview = (entry) => entry?.preview || entry?.text || entry?.attachment?.name || '';

/* «Черновик: …» в строке списка. В открытом чате черновик и так в поле ввода —
   строка показывает последнее сообщение; без человека (архив без обработки)
   черновиков нет вовсе. */
export const chatListDraft = (drafts, chatKey, { owner, open }) =>
    (owner == null || open ? '' : draftPreview(drafts?.[chatKey]));

export function useChatDrafts(owner) {
    return useSyncExternalStore(subscribeChatDrafts, () => ensure(owner), () => EMPTY);
}

// Только для тестов: модуль — синглтон вкладки.
export function resetChatDraftsForTests() {
    state = { owner: null, drafts: EMPTY };
    emit();
}
