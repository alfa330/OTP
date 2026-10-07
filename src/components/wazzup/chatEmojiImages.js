// Same Apple artwork source used by emoji-picker-react, pinned for a one-year immutable CDN cache.
export const APPLE_EMOJI_BASE = 'https://cdn.jsdelivr.net/npm/emoji-datasource-apple@16.0.0/img/apple/64/';
const failedImages = new Set();

export function nativeEmojiImage(unified) {
    if (!/^[a-f0-9]{4,6}(?:-[a-f0-9]{4,6})*$/i.test(unified || '')) return '';
    const codes = unified.split('-').map((code) => Number.parseInt(code, 16));
    if (codes.some((code) => code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff))) return '';
    const emoji = String.fromCodePoint(...codes).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><text x="32" y="50" text-anchor="middle" font-size="52" font-family="Apple Color Emoji,Segoe UI Emoji,Noto Color Emoji,sans-serif">${emoji}</text></svg>`)}`;
}

export function appleEmojiUrl(unified) {
    return failedImages.has(unified) ? nativeEmojiImage(unified) : `${APPLE_EMOJI_BASE}${unified}.png`;
}

// Keep an emoji selectable if that PNG/CDN fails instead of the library hiding it.
export function handleEmojiImageError(event) {
    const img = event.target;
    if (img?.tagName !== 'IMG' || !img.src.startsWith(APPLE_EMOJI_BASE)) return;
    const unified = img.src.slice(APPLE_EMOJI_BASE.length).replace(/\.png$/, '');
    const fallback = nativeEmojiImage(unified);
    if (!fallback) return;
    event.stopPropagation();
    failedImages.add(unified);
    img.src = fallback;
}

export function createEmojiImageWarmup(codes, makeImage = () => new Image()) {
    let started = false;
    return () => {
        if (started || typeof globalThis.Image === 'undefined') return;
        started = true;
        const queue = [...new Set(codes)].slice(0, 24);
        const next = () => {
            const unified = queue.shift();
            if (!unified) return;
            const img = makeImage();
            let completed = false;
            let timeout;
            const done = () => {
                if (completed) return;
                completed = true;
                clearTimeout(timeout);
                img.onload = img.onerror = null;
                next();
            };
            img.onload = img.onerror = done;
            img.decoding = 'async';
            img.fetchPriority = 'low';
            timeout = setTimeout(done, 8000);
            img.src = appleEmojiUrl(unified);
        };
        for (let i = 0; i < 4; i += 1) next();
    };
}
