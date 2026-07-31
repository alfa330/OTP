/*
 * Разбор Telegram-разметки в дерево — чтобы показывать текст заявки так, как он
 * придёт в чат, а не сырыми тегами.
 *
 * Текст приходит от ИИ, поэтому вставлять его как HTML нельзя: результат разбора —
 * дерево, из которого UI собирает React-элементы (см. ITTicketModal.jsx).
 * Неизвестные теги остаются обычным текстом — ничего не пропадает и ничего
 * не исполняется.
 */

// Telegram понимает ограниченный набор тегов; синонимы сводим к одному виду.
export const TG_TAGS = {
    b: 'b', strong: 'b',
    i: 'i', em: 'i',
    u: 'u',
    s: 's', strike: 's', del: 's',
    code: 'code',
    pre: 'pre',
};

const ENTITIES = [
    [/&lt;/g, '<'],
    [/&gt;/g, '>'],
    [/&quot;/g, '"'],
    [/&#39;/g, "'"],
    [/&nbsp;/g, ' '],
    [/&amp;/g, '&'], // последним: иначе «&amp;lt;» развернётся дважды
];

export const decodeEntities = (value) =>
    ENTITIES.reduce((acc, [re, ch]) => acc.replace(re, ch), String(value ?? ''));

const TAG_RE = /<\/?([a-zA-Z]+)[^>]*>/g;

/**
 * @param {string} raw текст с разметкой Telegram
 * @returns {Array<string|{tag: string, children: Array}>} плоский список узлов
 */
export function parseTelegramText(raw) {
    const text = String(raw ?? '');
    const root = { tag: null, children: [] };
    const stack = [root];
    let last = 0;

    const pushText = (chunk) => {
        if (chunk) stack[stack.length - 1].children.push(decodeEntities(chunk));
    };

    TAG_RE.lastIndex = 0;
    let m = TAG_RE.exec(text);
    while (m !== null) {
        const tag = TG_TAGS[m[1].toLowerCase()];
        if (tag) {
            pushText(text.slice(last, m.index));
            last = m.index + m[0].length;
            if (m[0][1] === '/') {
                // Закрываем только реально открытый тег: лишний </b> — не повод
                // схлопнуть всё дерево.
                const idx = stack.map((n) => n.tag).lastIndexOf(tag);
                if (idx > 0) stack.length = idx;
            } else {
                const node = { tag, children: [] };
                stack[stack.length - 1].children.push(node);
                stack.push(node);
            }
        }
        m = TAG_RE.exec(text);
    }
    pushText(text.slice(last));
    return root.children;
}

/** Тот же текст без разметки — для мест, где нужен только смысл. */
export function stripTelegramTags(raw) {
    const walk = (nodes) => nodes.map(
        (n) => (typeof n === 'string' ? n : walk(n.children)),
    ).join('');
    return walk(parseTelegramText(raw));
}
