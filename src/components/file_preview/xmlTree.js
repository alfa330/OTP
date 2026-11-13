/* Маленький разборщик XML для частей пакетов Office (docx, xlsx, pptx).

   Почему свой, а не DOMParser: разбор документов проверяется тестами в node,
   где DOMParser нет. Части Office — правильно сформированный XML без DTD,
   поэтому хватает одного регулярного выражения на токены. Узел — объект
   { name, local, attrs, children }, текст — строки среди children.

   Пробельный текст между тегами отбрасывается (в Office он ничего не значит),
   кроме текста внутри <…:t> — там пробел и есть содержимое. */

const TOKEN = /<(\/?)([A-Za-z_][\w.:-]*)((?:\s+[^\s=>/]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*(\/?)>|<!\[CDATA\[([\s\S]*?)\]\]>|<!--[\s\S]*?-->|<[?!][\s\S]*?>|([^<]+)/g;
const ATTR = /([^\s=>/]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
const ENTITIES = { lt: '<', gt: '>', amp: '&', quot: '"', apos: "'" };

export const localName = (name) => {
    const at = name.indexOf(':');
    return at >= 0 ? name.slice(at + 1) : name;
};

export function decodeEntities(text) {
    if (text.indexOf('&') < 0) return text;
    return text.replace(/&(#x[0-9a-fA-F]+|#\d+|[A-Za-z]+);/g, (whole, entity) => {
        if (entity[0] === '#') {
            const code = entity[1] === 'x' || entity[1] === 'X'
                ? parseInt(entity.slice(2), 16) : parseInt(entity.slice(1), 10);
            try {
                return String.fromCodePoint(code);
            } catch {
                return whole;
            }
        }
        return ENTITIES[entity] ?? whole;
    });
}

const parseAttrs = (raw) => {
    const attrs = {};
    if (!raw) return attrs;
    const pattern = new RegExp(ATTR.source, 'g');
    let match;
    while ((match = pattern.exec(raw))) {
        attrs[match[1]] = decodeEntities(match[2] ?? match[3] ?? '');
    }
    return attrs;
};

export function parseXml(source) {
    const root = { name: '#root', local: '#root', attrs: {}, children: [] };
    const stack = [root];
    const pattern = new RegExp(TOKEN.source, 'g');
    const text = String(source || '');
    let match;
    while ((match = pattern.exec(text))) {
        const [, closing, name, rawAttrs, selfClosing, cdata, chars] = match;
        const top = stack[stack.length - 1];
        if (name) {
            if (closing) {
                // Терпимо к перекосам: закрываем до ближайшего одноимённого.
                for (let index = stack.length - 1; index > 0; index -= 1) {
                    if (stack[index].name === name) {
                        stack.length = index;
                        break;
                    }
                }
                continue;
            }
            const node = { name, local: localName(name), attrs: parseAttrs(rawAttrs), children: [] };
            top.children.push(node);
            if (!selfClosing) stack.push(node);
        } else if (cdata !== undefined) {
            top.children.push(cdata);
        } else if (chars !== undefined && (top.local === 't' || /\S/.test(chars))) {
            top.children.push(decodeEntities(chars));
        }
    }
    return root;
}

const isElement = (node) => node && typeof node === 'object';

export const elements = (node) => (node ? node.children.filter(isElement) : []);

export const child = (node, local) => (node ? node.children.find((item) => isElement(item) && item.local === local) || null : null);

export const childrenOf = (node, local) => (node ? node.children.filter((item) => isElement(item) && item.local === local) : []);

/** Значение атрибута по полному имени или по имени без префикса (w:val → val). */
export function attr(node, name) {
    if (!node) return undefined;
    if (Object.prototype.hasOwnProperty.call(node.attrs, name)) return node.attrs[name];
    for (const key of Object.keys(node.attrs)) {
        if (localName(key) === name) return node.attrs[key];
    }
    return undefined;
}

/** Ссылка на связь пакета (r:id). У элемента бывает и свой обычный id —
 *  <p:sldId id="256" r:id="rId2"> — и путать их нельзя. */
export function relId(node) {
    if (!node) return undefined;
    for (const key of Object.keys(node.attrs)) {
        if (key.includes(':') && localName(key) === 'id') return node.attrs[key];
    }
    return node.attrs.id;
}

/** Все потомки с данным именем — в порядке документа. */
export function findAll(node, local, out = []) {
    if (!node) return out;
    for (const item of node.children) {
        if (!isElement(item)) continue;
        if (item.local === local) out.push(item);
        findAll(item, local, out);
    }
    return out;
}

export function findFirst(node, local) {
    if (!node) return null;
    for (const item of node.children) {
        if (!isElement(item)) continue;
        if (item.local === local) return item;
        const deep = findFirst(item, local);
        if (deep) return deep;
    }
    return null;
}

export function textContent(node) {
    if (!node) return '';
    let out = '';
    for (const item of node.children) out += typeof item === 'string' ? item : textContent(item);
    return out;
}

/** Флаг Office: <w:b/> — да, <w:b w:val="0"/> или "false" — нет. */
export function onOff(node) {
    if (!node) return false;
    const value = attr(node, 'val');
    return value === undefined || !['0', 'false', 'off', 'none'].includes(String(value).toLowerCase());
}
