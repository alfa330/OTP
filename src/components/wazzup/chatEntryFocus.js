/* Вход в чат — сразу к набору: фокус в поле сообщения, курсор в конце черновика.
 *
 * Фокус не ставится:
 * - без мыши (телефон, планшет): он открыл бы экранную клавиатуру поверх переписки;
 * - когда человек уже в другом поле или в открытом окне. Поле сообщения
 *   появляется и без входа в чат — закрылась заметка, чату стало можно писать —
 *   и отбирать фокус у набираемого в это время нельзя.
 * preventScroll: фокус не должен сдвигать ни ленту, ни страницу. */
const TYPING_TARGET = 'input, textarea, select, [contenteditable]:not([contenteditable="false"])';

export function focusOnChatEntry(field) {
    const doc = field?.ownerDocument;
    if (!doc || typeof field.focus !== 'function') return false;
    if (!doc.defaultView?.matchMedia?.('(hover: hover) and (pointer: fine)').matches) return false;
    const active = doc.activeElement;
    if (active?.matches?.(TYPING_TARGET) || active?.closest?.('[role="dialog"]')) return false;
    field.focus({ preventScroll: true });
    const end = field.value.length;
    field.setSelectionRange?.(end, end);
    return true;
}
