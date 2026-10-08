const ATTACHMENTS = {
    image: 'Фото', video: 'Видео', audio: 'Голосовое сообщение', document: 'Документ',
    geo: 'Геолокация', vcard: 'Контакт', missing_call: 'Пропущенный звонок',
};

export function canReplyOnDoubleClick(event) {
    // Preserve embedded controls (quoted-message links, media and downloads).
    return !event.target?.closest?.('button, a, input, textarea, select, audio, video, [contenteditable="true"]');
}

export function messageQuote(message, messagesById) {
    const messageId = message?.replyToMessageId;
    if (!messageId || messageId === message.messageId) return null;
    const original = messagesById.get(messageId);
    const text = original?.isDeleted ? 'Сообщение удалено'
        : original?.text || message.replyText || ATTACHMENTS[original?.type] || 'Исходное сообщение';
    return {
        messageId,
        text,
        author: original?.authorName || message.replyAuthorName
            || (original ? (original.isEcho ? 'Оператор' : 'Клиент') : 'Ответ на сообщение'),
    };
}

// Message rows remain ordered vertically. Read only log(n) rectangles per
// animation frame, including the partly visible message at the top.
export function firstVisibleMessage(nodes, viewportTop) {
    let start = 0;
    let end = nodes.length;
    while (start < end) {
        const middle = (start + end) >>> 1;
        if (nodes[middle].getBoundingClientRect().bottom <= viewportTop) start = middle + 1;
        else end = middle;
    }
    return nodes[start] || null;
}
