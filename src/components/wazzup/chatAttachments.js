export const ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024;

export function attachmentName(message) {
    let name = message?.fileName || message?.filename || '';
    if (!name && message?.contentUri) {
        try {
            const url = new URL(message.contentUri);
            name = url.searchParams.get('filename') || url.searchParams.get('fileName')
                || decodeURIComponent(url.pathname.split('/').pop() || '');
        } catch { /* A malformed vendor URL must not break the conversation. */ }
    }
    name = String(name).replace(/[\\/\x00-\x1f]/g, '_').trim().slice(0, 180);
    // Opaque storage IDs are not useful document titles.
    return /\.[a-z0-9]{2,6}$/i.test(name) ? name : '';
}

export function attachmentPreviewKind(message) {
    if (!message?.contentUri || message.isDeleted) return null;
    const mime = String(message.mimeType || message.contentType || '').split(';')[0].toLowerCase();
    const name = attachmentName(message);
    if (message.type === 'video' || /^video\//.test(mime) || /\.(?:mp4|webm|mov|m4v|ogv)$/i.test(name)) return 'video';
    if (mime === 'application/pdf' || /\.pdf$/i.test(name)) return 'pdf';
    if (message.type === 'image' || /^image\/(?:jpeg|png|webp|gif|bmp)$/.test(mime)
        || /\.(?:jpe?g|png|webp|gif|bmp)$/i.test(name)) return 'image';
    // All documents participate in a media group. Known unsupported formats get
    // a file card; without a filename the authenticated endpoint checks the MIME.
    if (message.type === 'document') return 'document';
    return null;
}

export function pdfPageText(content) {
    const lines = [];
    let line = '';
    let previous = null;
    for (const item of content?.items || []) {
        if (typeof item.str !== 'string') continue;
        const y = item.transform?.[5];
        const previousY = previous?.transform?.[5];
        const differentLine = Number.isFinite(y) && Number.isFinite(previousY)
            && Math.abs(y - previousY) > Math.max(2, Math.abs(item.height || 0) * 0.4);
        if (differentLine && line) { lines.push(line.trimEnd()); line = ''; }
        if (line && item.str && !/\s$/.test(line) && !/^\s/.test(item.str)) {
            const gap = item.transform?.[4] - ((previous?.transform?.[4] || 0) + (previous?.width || 0));
            if (!Number.isFinite(gap) || gap > Math.max(0.5, Math.abs(item.height || 0) * 0.12)) line += ' ';
        }
        line += item.str;
        if (item.hasEOL) { lines.push(line.trimEnd()); line = ''; }
        previous = item;
    }
    if (line) lines.push(line.trimEnd());
    return lines.join('\n').trim();
}

export function boundedCanvasSize(width, height, preferredScale = 1, maxPixels = 8_000_000, maxEdge = 8192) {
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
        throw new Error('Некорректный размер страницы');
    }
    const scale = Math.min(Math.max(0.01, preferredScale), Math.sqrt(maxPixels / width / height),
        maxEdge / width, maxEdge / height);
    return { width: Math.max(1, Math.floor(width * scale)), height: Math.max(1, Math.floor(height * scale)), scale };
}

export function saveAttachment(url, filename, ownerDocument = document) {
    const anchor = ownerDocument.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = 'noopener';
    ownerDocument.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
}
