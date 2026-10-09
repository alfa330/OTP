// Keep loaded history while applying webhook updates, including edits/deletions.
const STATUS_ORDER = { queued: 0, pending: 0, sent: 1, error: 2, delivered: 3, read: 4 };

// Статус доставки не откатывается: опоздавшее «доставлено» не гасит «прочитано».
export const laterStatus = (current, next) => ((STATUS_ORDER[current] ?? -1) > (STATUS_ORDER[next] ?? -1) ? current : next);

export const GLOBAL_CHANNEL_IDS = new Set([
    '99df6893-fb6b-4e1d-a78e-9e6e6b37abb2',
    'a4bccb5e-5d41-483d-b7c1-a1079685577d',
]);

export const pilotChatKey = (account, chat) => chat
    ? JSON.stringify([account, chat.channelId, chat.chatId]) : '';

export const pilotDraftStorageKey = (chat) => `icore.wazzup.pilot.draft.${pilotChatKey('op', chat)}`;

export const newClientMessageId = () => {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

// Состояние очереди (sendQueue.js) → значок пузыря: до ответа сервера и пока
// Wazzup не сообщил «отправлено» — одни и те же часы, как в WhatsApp.
const OUTBOX_STATUS = { queued: 'queued', sending: 'queued', checking: 'queued', sent: 'pending',
    failed: 'failed', unknown: 'unknown' };

const attachmentType = (mime) => {
    const kind = String(mime || '').split('/')[0];
    return ['image', 'video', 'audio'].includes(kind) ? kind : 'document';
};

/* Свои ещё не подтверждённые архивом сообщения — пока не придёт строка архива
   той же отправки. Узнаём её по messageId (после ответа сервера) или по
   clientMessageId (строка может прийти по SSE раньше ответа). Сообщение в пути
   закреплено в конце ленты (pinned): оно новее загруженной истории, и порядок —
   порядок набора, часы браузера не важны. Сбой (failed/unknown) встаёт на своё
   время среди остальных — иначе вчерашний отказ висел бы под сегодняшней
   перепиской. */
export const outboxMessages = (thread, outbox) => {
    if (!thread || !outbox?.length) return [];
    const ids = new Set();
    const clientIds = new Set();
    for (const message of thread) {
        ids.add(message.messageId);
        if (message.clientMessageId) clientIds.add(message.clientMessageId);
    }
    return outbox
        .filter((item) => !clientIds.has(item.clientMessageId) && !(item.messageId && ids.has(item.messageId)))
        .sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)))
        .map((item) => ({
            messageId: `local:${item.clientMessageId}`, clientMessageId: item.clientMessageId,
            dt: item.createdAt, isEcho: true, type: item.attachment ? attachmentType(item.attachment.mime) : 'text',
            text: item.displayText || '', authorName: item.authorName || null, authorId: null,
            status: OUTBOX_STATUS[item.state] || 'queued',
            replyToMessageId: item.replyToMessageId || null,
            replyText: item.reply?.text || null, replyAuthorName: item.reply?.authorName || null,
            local: { state: item.state, error: item.error || '', pinned: !['failed', 'unknown'].includes(item.state),
                editable: item.state === 'failed' && !item.attachmentId && item.text === item.displayText },
        }));
};

export const classifyPilotSendFailure = (error) => {
    const response = error?.response;
    const data = response?.data || {};
    // Only an explicit rejection lets the operator edit and start a new send.
    // Timeouts and unknown server results keep the original idempotency key.
    const unknown = data.state === 'unknown' || (data.state !== 'failed'
        && (!response || response.status >= 500 || [408, 409, 425].includes(response.status)));
    return {
        state: unknown ? 'unknown' : 'failed',
        message: typeof data.error === 'string' ? data.error
            : unknown ? 'Ответ сервера не получен. Проверьте отправку, прежде чем писать снова.'
                : 'Сообщение не отправлено. Проверьте текст и попробуйте ещё раз.',
    };
};

export const mergePilotMessages = (previous = [], incoming = []) => {
    const byId = new Map((previous || []).map((message) => [message.messageId, message]));
    for (const message of incoming || []) {
        if (!message?.messageId) continue;
        const old = byId.get(message.messageId);
        const merged = { ...old, ...message };
        // A stale snapshot must not turn a read receipt back into "sent".
        if (old && (STATUS_ORDER[old.status] ?? -1) > (STATUS_ORDER[message.status] ?? -1)) {
            merged.status = old.status;
        }
        byId.set(message.messageId, merged);
    }
    return [...byId.values()].sort((a, b) => {
        const delta = new Date(a.dt).getTime() - new Date(b.dt).getTime();
        return (Number.isFinite(delta) ? delta : 0)
            || String(a.messageId).localeCompare(String(b.messageId));
    });
};

// SSE can split a line or a UTF-8 character between network chunks. The caller
// decodes bytes with TextDecoder({stream:true}); this parser retains partial frames.
export const splitPilotEvents = (input) => {
    const frames = [];
    let buffer = input;
    let boundary;
    while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const raw = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        let event = 'message';
        let id = null;
        const data = [];
        for (const line of raw.split(/\r?\n/)) {
            if (line.startsWith('event:')) event = line.slice(6).trim();
            else if (line.startsWith('id:')) id = line.slice(3).trim();
            else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
        }
        if (data.length || raw.includes(': connected')) frames.push({ event, id, data: data.join('\n') });
    }
    return { frames, buffer };
};
