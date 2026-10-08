import { attachmentPreviewKind } from './chatAttachments.js';

const MAX_GROUP_SIZE = 100;
const MAX_GAP_MS = 10 * 60 * 1000;
const scopeKey = (value) => String(value ?? '');
const authorNameKey = (name) => String(name || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase('ru-RU');

function sameScope(left, right) {
    return ['account', 'channelId', 'chatId'].every((key) => scopeKey(left[key]) === scopeKey(right[key]));
}

function sameSender(left, right) {
    if (Boolean(left.isEcho) !== Boolean(right.isEcho)) return false;
    // Incoming messages in these personal chats belong to the same driver.
    if (!left.isEcho) return true;
    const leftId = String(left.authorId ?? '').trim();
    const rightId = String(right.authorId ?? '').trim();
    if (leftId && rightId) return leftId === rightId;
    const leftName = authorNameKey(left.authorName);
    return Boolean(leftName) && leftName === authorNameKey(right.authorName);
}

function mediaMessage(message) {
    return Boolean(message && !message._day && !message._note && attachmentPreviewKind(message));
}

function timestamp(message) {
    if (typeof message.dt !== 'string' && typeof message.dt !== 'number') return NaN;
    if (message.dt === '') return NaN;
    return new Date(message.dt).getTime();
}

function consecutive(left, right) {
    if (!mediaMessage(left) || !mediaMessage(right) || !sameScope(left, right) || !sameSender(left, right)) return false;
    const gap = timestamp(right) - timestamp(left);
    return Number.isFinite(gap) && gap >= 0 && gap <= MAX_GAP_MS;
}

// Work only with the loaded timeline: never fetch neighboring customer files
// or cross a text message, internal comment or day separator to build a group.
export function buildAttachmentGroup(thread, selected) {
    if (!selected) return [];
    if (!Array.isArray(thread) || !selected.messageId || !mediaMessage(selected)) return [selected];
    const index = thread.findIndex((message) => message?.messageId === selected.messageId && sameScope(message, selected));
    if (index === -1) return [selected];
    let start = index;
    let end = index + 1;
    while (start > 0 && consecutive(thread[start - 1], thread[start])) start -= 1;
    while (end < thread.length && consecutive(thread[end - 1], thread[end])) end += 1;
    // Bound the thumbnail strip, retaining the selected file and chronological
    // neighbors on both sides even when a very long batch is already loaded.
    if (end - start > MAX_GROUP_SIZE) {
        start = Math.min(Math.max(start, index - Math.floor(MAX_GROUP_SIZE / 2)), end - MAX_GROUP_SIZE);
        end = start + MAX_GROUP_SIZE;
    }
    return thread.slice(start, end);
}
