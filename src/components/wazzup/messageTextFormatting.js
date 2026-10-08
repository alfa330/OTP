// Decode the entities commonly produced by CRM/plain-text exports without ever
// interpreting message contents as HTML. One pass preserves intentionally escaped
// entity examples such as &amp;gt; and leaves unknown/malformed entities unchanged.
const namedEntities = {
    amp: '&', AMP: '&', lt: '<', LT: '<', gt: '>', GT: '>',
    quot: '"', QUOT: '"', apos: "'", nbsp: '\u00a0',
    ensp: '\u2002', emsp: '\u2003', thinsp: '\u2009',
    ndash: '–', mdash: '—', hellip: '…', laquo: '«', raquo: '»',
    lsquo: '‘', rsquo: '’', ldquo: '“', rdquo: '”',
    copy: '©', reg: '®', trade: '™', bull: '•', middot: '·', euro: '€',
};

export function decodeMessageText(value) {
    const text = typeof value === 'string' ? value : String(value ?? '');
    return text.replace(/&(#(?:[xX][\da-fA-F]+|\d+)|[a-zA-Z]+);/g, (entity, code) => {
        if (code[0] !== '#') return Object.hasOwn(namedEntities, code) ? namedEntities[code] : entity;
        const hex = code[1]?.toLowerCase() === 'x';
        const point = Number.parseInt(code.slice(hex ? 2 : 1), hex ? 16 : 10);
        if (!Number.isFinite(point) || point <= 0 || point > 0x10ffff || (point >= 0xd800 && point <= 0xdfff)) return entity;
        return String.fromCodePoint(point);
    });
}

const trailingPunctuation = /[.,!?;:…]$/u;
const closingBrackets = { ')': '(', ']': '[', '}': '{' };

function trimLinkEnd(candidate) {
    const counts = new Map();
    for (const char of candidate) {
        if ('()[]{}'.includes(char)) counts.set(char, (counts.get(char) || 0) + 1);
    }
    let end = candidate.length;
    while (end) {
        const last = candidate[end - 1];
        if (trailingPunctuation.test(last)) { end -= 1; continue; }
        const opening = closingBrackets[last];
        if (opening && (counts.get(last) || 0) > (counts.get(opening) || 0)) {
            counts.set(last, counts.get(last) - 1);
            end -= 1;
            continue;
        }
        break;
    }
    return candidate.slice(0, end);
}

function messageLinkHref(text) {
    const bare = /^www\./i.test(text);
    const href = bare ? `https://${text}` : text;
    try {
        const url = new URL(href);
        if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) return null;
        if (bare && !/^www\..+\.[^.]+$/i.test(url.hostname)) return null;
        return href;
    } catch {
        return null;
    }
}

export function tokenizeMessageLinks(value) {
    const text = decodeMessageText(value);
    if (!text) return [];
    const tokens = [];
    const pattern = /(?:https?:\/\/|www\.)[^\s\u0000-\u001f\u007f<>"'`«»“”‘’]+/giu;
    let cursor = 0;
    for (const match of text.matchAll(pattern)) {
        // Do not turn the www part of an email, domain or path into another link.
        if (match.index > 0 && /[\p{L}\p{N}_@./-]/u.test(text[match.index - 1])) continue;
        const label = trimLinkEnd(match[0]);
        const href = messageLinkHref(label);
        if (!href) continue;
        if (match.index > cursor) tokens.push({ text: text.slice(cursor, match.index) });
        tokens.push({ text: label, href });
        cursor = match.index + label.length;
    }
    if (cursor < text.length) tokens.push({ text: text.slice(cursor) });
    return tokens;
}
