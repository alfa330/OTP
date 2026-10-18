import { useSyncExternalStore } from 'react';

/* Счётчик «Чатов ОП» в боковой панели портала: сколько сообщений клиентов ждут
 * ответа команды — то же число, что «Ожидают ответа · N» в самом разделе
 * (просьба владельца 18.10.2026: «до 9+»).
 *
 * Источник у числа один из двух, и одновременно они не работают:
 *   * открыт раздел на вкладке «Чаты» — считает он сам (useSharedChatUnread
 *     поверх своего живого потока) и только сообщает число сюда;
 *   * раздела нет — WazzupWaitingKeeper на весь портал держит свой поток,
 *     только событий счётчика (?only=unread), и пишет число сюда.
 * Второго потока при открытом разделе нет: сторож молчит, пока раздел
 * «держит» счётчик (claimSectionWaiting).
 *
 * Модуль — синглтон без React-состояния: подписка через useSyncExternalStore,
 * как у workspaceStore.js. */

let count = 0;              // последнее известное число; 0 — нет (или не положено)
let sectionClaims = 0;      // раздел открыт на «Чатах» и считает сам
const listeners = new Set();

const emit = () => listeners.forEach((listener) => listener());
const subscribe = (listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
};

/* Раздел взялся считать сам. Возвращает отказ — на размонтирование, смену
   аккаунта или уход с вкладки «Чаты». Число при этом остаётся прежним: сторож
   подхватит с него, без мигания нулём. */
export function claimSectionWaiting() {
    sectionClaims += 1;
    emit();
    let released = false;
    return () => {
        if (released) return;
        released = true;
        sectionClaims = Math.max(0, sectionClaims - 1);
        emit();
    };
}

// from — 'section' или 'keeper': сторож не перебивает раздел, пока тот считает.
// null — числа нет (обработка не открыта): бейдж прячется, как при нуле.
export function publishWaiting(value, from = 'section') {
    if (from === 'keeper' && sectionClaims > 0) return;
    const next = Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
    if (next === count) return;
    count = next;
    emit();
}

export const sectionCountsWaiting = () => sectionClaims > 0;
export const currentWaiting = () => count;

export const useChatWaitingCount = () => useSyncExternalStore(subscribe, currentWaiting, currentWaiting);
export const useSectionCountsWaiting = () => useSyncExternalStore(subscribe, sectionCountsWaiting, sectionCountsWaiting);

// В боковой панели — не больше одной цифры: дальше «9+».
export const waitingBadgeLabel = (value) => (value > 9 ? '9+' : String(value));

// Для тестов и смены учётки: всё с нуля.
export function resetWaiting() {
    count = 0;
    sectionClaims = 0;
    emit();
}
