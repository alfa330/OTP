import JSZip from 'jszip';
import DOMPurify from 'dompurify';
import {
    bookCss, cleanInlineStyle, decodeAnchor, dropCapText, isAsideParagraph, resolvePath,
} from './epubText.js';

/*
 * Загрузчик книги EPUB для ридера «Библиотеки» (задача #282).
 *
 * ПОЧЕМУ НЕ epub.js. Тот рисует главу в изолированном iframe, и из него не
 * достать соседнюю страницу для перелистывания, а Safari вдобавок не отдаёт
 * оттуда касания. Здесь книга распаковывается в браузере (JSZip), глава
 * очищается (DOMPurify) и ложится в теневой DOM ридера — страницы, жесты и
 * загиб листа полностью наши.
 *
 * БЕЗОПАСНОСТЬ. Разметка книги — чужой HTML, и живёт она теперь в документе
 * портала, без iframe. Поэтому:
 *   - DOMPurify вырезает скрипты, обработчики, формы и встраиваемое;
 *   - адреса переписываются на ОЧИЩЕННОМ дереве, а не на исходном XML: XHTML
 *     и HTML разбирают одну строку по-разному (CDATA), и картинка, спрятанная
 *     от переписывания в одном разборе, всплывала бы в другом;
 *   - в итоге любой адрес — blob: книги, #, или ничего; внешние ссылки живут
 *     в data-lr-ext и открываются только нажатием, в новой вкладке;
 *   - blob-файлы получают безопасный тип: растровые картинки — свой, SVG —
 *     только очищенный, остальное — octet-stream. SVG со скриптом, открытый
 *     через «Открыть картинку в новой вкладке», иначе исполнился бы от имени
 *     портала;
 *   - распаковка ограничена по размеру: «zip-бомба» в картинке уронила бы
 *     вкладку у каждого, кто открыл главу.
 *
 * ПОРЯДОК ГЛАВ — ровно как у сервера (library/epub.py): каждый itemref, даже
 * ссылка в пустоту, — отдельная глава. На этом совпадении держатся номера
 * страниц и проценты.
 */

const XLINK = 'http://www.w3.org/1999/xlink';

/* Пределы распаковки: глава — как у сервера (MAX_DOCUMENT_BYTES), картинка —
   с запасом под иллюстрацию во всю страницу. */
const TEXT_LIMIT = 30 * 1024 * 1024;
const RESOURCE_LIMIT = 25 * 1024 * 1024;

const RASTER_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif', 'image/bmp']);

const PURIFY_CONFIG = {
    USE_PROFILES: { html: true, svg: true },
    FORBID_TAGS: ['script', 'style', 'link', 'meta', 'iframe', 'object', 'embed', 'form', 'input',
        'button', 'textarea', 'select', 'base', 'audio', 'video', 'source', 'track', 'foreignObject'],
    FORBID_ATTR: ['srcset', 'background', 'poster', 'formaction', 'action', 'ping'],
    ADD_ATTR: ['xlink:href', 'epub:type'],
    RETURN_DOM_FRAGMENT: true,
};

const SVG_PURIFY_CONFIG = {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ['foreignObject', 'a', 'script', 'use'],
};

/* Атрибуты-адреса, которые после переписывания обязаны вести только в книгу. */
const URL_ATTRS = ['src', 'href', 'xlink:href', 'background', 'poster', 'action', 'formaction', 'data', 'codebase', 'srcset'];
const isExternal = (value) => /^[a-z][a-z0-9+.-]*:/i.test(value) || /^\/\//.test(value);

const parseXml = (text, type = 'application/xml') => {
    const doc = new DOMParser().parseFromString(text, type);
    return doc.getElementsByTagName('parsererror').length ? null : doc;
};

const byLocalName = (root, name) => Array.from(root.getElementsByTagName('*'))
    .filter((node) => (node.localName || node.nodeName).toLowerCase() === name);

/* Распаковать запись архива, не больше limit байт. Размер в заголовке zip
   «бомба» занижает, поэтому считаем то, что реально распаковалось. */
const readEntry = (entry, limit) => new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    let settled = false;
    const stream = entry.internalStream('uint8array');
    stream.on('data', (chunk) => {
        if (settled) return;
        size += chunk.length;
        if (size > limit) {
            settled = true;
            stream.pause();
            reject(new Error('Часть книги слишком большая'));
            return;
        }
        chunks.push(chunk);
    }).on('error', (error) => {
        if (!settled) { settled = true; reject(error); }
    }).on('end', () => {
        if (settled) return;
        settled = true;
        const out = new Uint8Array(size);
        let offset = 0;
        chunks.forEach((chunk) => { out.set(chunk, offset); offset += chunk.length; });
        resolve(out);
    }).resume();
});

/* Текст — как у сервера: UTF-16 по метке порядка байтов, иначе UTF-8 с
   заменой битых байтов (library/epub.py: _decode). */
const decodeText = (bytes) => {
    if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder('utf-16le').decode(bytes);
    if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder('utf-16be').decode(bytes);
    return new TextDecoder('utf-8').decode(bytes);
};

/* Типографские пометки для стиля страницы (readerEngine.js: BASE_CSS).
   Селектором их не угадать: «первый абзац после заголовка» ловит и эпиграф, и
   строку-дату, а «картинка» — и значок посреди фразы.
     lr-img-block — картинка одна в своём блоке: по центру отдельной строкой;
     lr-dropcap   — первый абзац текста главы: буквица. */
const BLOCK_HOLDERS = 'p, div, figure, center, section, li, td, blockquote';

/* Разрыв страницы перед самым началом главы (`.chapter { page-break-before:
   always }` — так устроены книги Gutenberg и Calibre). Chrome его не
   замечает, а WebKit (Safari, iPhone) делает из него пустую первую колонку:
   глава начиналась бы с чистой левой страницы, заголовок — справа.
     lr-lead       — всё, внутри чего начинается текст главы: без разрыва перед;
     lr-lead-empty — пустые элементы до него: без разрывов вовсе. */
const markLead = (fragment) => {
    const walker = document.createTreeWalker(fragment, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
        acceptNode: (node) => {
            if (node.nodeType === Node.TEXT_NODE) return node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
            return /^(img|image|svg|video|hr|table)$/i.test(node.localName) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
        },
    });
    const first = walker.nextNode();
    if (!first) return;
    for (const element of Array.from(fragment.querySelectorAll('*'))) {
        if (element === first || element.contains(first)) {
            element.classList.add('lr-lead');
            continue;
        }
        // eslint-disable-next-line no-bitwise
        if (!(element.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING)) break;
        element.classList.add('lr-lead', 'lr-lead-empty');
    }
};

const markTypography = (fragment) => {
    markLead(fragment);
    fragment.querySelectorAll('img').forEach((img) => {
        const holder = img.parentElement ? img.parentElement.closest(BLOCK_HOLDERS) : null;
        const alone = !holder || (!String(holder.textContent || '').trim()
            && holder.querySelectorAll('img, svg').length === 1);
        if (alone) img.classList.add('lr-img-block');
    });
    const heading = fragment.querySelector('h1, h2, h3');
    if (!heading) return;
    let asides = 0;
    for (const paragraph of Array.from(fragment.querySelectorAll('p'))) {
        // eslint-disable-next-line no-bitwise
        if (!(heading.compareDocumentPosition(paragraph) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
        if (paragraph.closest('h1, h2, h3, h4, h5, h6, table, li')) continue;
        const text = String(paragraph.textContent || '').trim();
        if (!text) continue;
        const aside = paragraph.closest('blockquote') || isAsideParagraph({
            className: paragraph.getAttribute('class') || '',
            parentClass: paragraph.parentElement?.getAttribute('class') || '',
            align: paragraph.getAttribute('align') || '',
            style: paragraph.getAttribute('style') || '',
        });
        if (aside) {
            asides += 1;
            if (asides > 6) return;
            continue;
        }
        if (dropCapText(text) && !paragraph.querySelector('img, svg')) paragraph.classList.add('lr-dropcap');
        return;
    }
};

export async function openEpub(data) {
    const zip = await JSZip.loadAsync(data);
    const files = new Map();
    zip.forEach((path, entry) => { if (!entry.dir) files.set(path, entry); });
    const lower = new Map();
    files.forEach((_entry, path) => { if (!lower.has(path.toLowerCase())) lower.set(path.toLowerCase(), path); });
    const entryOf = (path) => files.get(path) || files.get(lower.get(String(path).toLowerCase()) || '');
    const readText = async (path) => {
        const entry = entryOf(path);
        return entry ? decodeText(await readEntry(entry, TEXT_LIMIT)) : null;
    };

    const containerText = await readText('META-INF/container.xml');
    const container = containerText && parseXml(containerText);
    const rootfile = container && byLocalName(container, 'rootfile')[0];
    const opfPath = rootfile?.getAttribute('full-path') || '';
    const opfText = opfPath ? await readText(opfPath) : null;
    const opf = opfText && parseXml(opfText);
    if (!opf) throw new Error('Не удалось прочитать книгу');
    const opfDir = opfPath.includes('/') ? opfPath.slice(0, opfPath.lastIndexOf('/')) : '';

    const manifest = new Map();
    byLocalName(opf, 'item').forEach((item) => {
        const id = item.getAttribute('id');
        const href = item.getAttribute('href');
        if (!id || !href) return;
        manifest.set(id, {
            href,
            path: resolvePath(opfDir, href),
            type: String(item.getAttribute('media-type') || '').toLowerCase(),
        });
    });
    const typeOfPath = new Map(Array.from(manifest.values()).map((item) => [item.path.toLowerCase(), item.type]));

    const spine = byLocalName(opf, 'itemref').map((ref) => {
        const item = manifest.get(ref.getAttribute('idref'));
        return {
            path: item ? item.path : '',
            href: item ? item.href : '',
            linear: Boolean(item) && (ref.getAttribute('linear') || 'yes') === 'yes',
        };
    });
    const indexOfPath = new Map();
    spine.forEach((item, index) => {
        if (item.path && !indexOfPath.has(item.path.toLowerCase())) indexOfPath.set(item.path.toLowerCase(), index);
    });

    let destroyed = false;
    // path -> Promise<blob-адрес | ''>: одновременные запросы делят один blob.
    const blobUrls = new Map();
    const resourceUrl = (path) => {
        const key = path.toLowerCase();
        if (!blobUrls.has(key)) {
            blobUrls.set(key, (async () => {
                const entry = entryOf(path);
                if (!entry || destroyed) return '';
                const declared = typeOfPath.get(key) || '';
                let blob;
                try {
                    const bytes = await readEntry(entry, RESOURCE_LIMIT);
                    if (destroyed) return '';
                    if (declared === 'image/svg+xml' || /\.svg$/i.test(path)) {
                        const clean = DOMPurify.sanitize(decodeText(bytes), SVG_PURIFY_CONFIG);
                        blob = new Blob([clean], { type: 'image/svg+xml' });
                    } else {
                        blob = new Blob([bytes], { type: RASTER_TYPES.has(declared) ? declared : 'application/octet-stream' });
                    }
                } catch {
                    return '';
                }
                return destroyed ? '' : URL.createObjectURL(blob);
            })());
        }
        return blobUrls.get(key);
    };

    /* Стиль книги: адреса url(...) — на blob, внешние — прочь. */
    const loadCss = async (cssPath, text) => {
        const dir = cssPath.includes('/') ? cssPath.slice(0, cssPath.lastIndexOf('/')) : '';
        const refs = new Map();
        String(text).replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/gi, (_m, _q, ref) => { refs.set(ref, null); return ''; });
        await Promise.all(Array.from(refs.keys()).map(async (ref) => {
            refs.set(ref, isExternal(ref) ? '' : await resourceUrl(resolvePath(dir, ref)));
        }));
        return bookCss(text, (ref) => refs.get(ref) || '');
    };

    const cache = new Map();
    const loadSection = (index) => {
        if (cache.has(index)) return cache.get(index);
        const promise = (async () => {
            const item = spine[index];
            const raw = item && item.path ? await readText(item.path) : null;
            if (raw === null) return { fragment: document.createDocumentFragment(), css: '', lang: '' };
            const type = typeOfPath.get(item.path.toLowerCase()) || 'application/xhtml+xml';
            const doc = (type !== 'text/html' && parseXml(raw, 'application/xhtml+xml'))
                || new DOMParser().parseFromString(raw, 'text/html');
            const dir = item.path.includes('/') ? item.path.slice(0, item.path.lastIndexOf('/')) : '';

            const cssParts = [];
            for (const node of Array.from(doc.querySelectorAll('link[rel~="stylesheet" i], style'))) {
                if (node.localName === 'style') {
                    // eslint-disable-next-line no-await-in-loop
                    cssParts.push(await loadCss(item.path, node.textContent || ''));
                } else {
                    const href = node.getAttribute('href') || '';
                    const cssPath = resolvePath(dir, href);
                    // eslint-disable-next-line no-await-in-loop
                    const text = isExternal(href) ? null : await readText(cssPath);
                    // eslint-disable-next-line no-await-in-loop
                    if (text !== null) cssParts.push(await loadCss(cssPath, text));
                }
            }

            const body = doc.body || doc.querySelector('body') || doc.documentElement;
            // Сначала очистка, потом — переписывание адресов на том же дереве.
            const fragment = DOMPurify.sanitize(body.innerHTML, PURIFY_CONFIG);

            // Картинки книги — blob-адресами, чужие — выбрасываем.
            await Promise.all(Array.from(fragment.querySelectorAll('img, image')).map(async (node) => {
                const svgImage = node.localName === 'image';
                const ref = svgImage
                    ? (node.getAttribute('href') || node.getAttributeNS(XLINK, 'href') || '')
                    : (node.getAttribute('src') || '');
                const url = !ref || isExternal(ref) ? '' : await resourceUrl(resolvePath(dir, ref));
                if (svgImage) {
                    node.removeAttribute('href');
                    if (url) node.setAttributeNS(XLINK, 'xlink:href', url); else node.removeAttributeNS(XLINK, 'href');
                } else if (url) {
                    node.setAttribute('src', url);
                } else {
                    node.removeAttribute('src');
                }
            }));

            // Ссылки (и SVG-ссылки, и <area>): внутренние ведут в главу книги,
            // внешние — только по нажатию и в новую вкладку. Своего href у них
            // не остаётся: нажатие не уведёт вкладку портала, а Tab и поиск по
            // странице не будут прокручивать колонки к ссылке.
            fragment.querySelectorAll('a, area').forEach((link) => {
                const href = link.getAttribute('href') || link.getAttributeNS(XLINK, 'href') || '';
                link.removeAttribute('href');
                link.removeAttributeNS(XLINK, 'href');
                if (!href) return;
                if (/^(https?|mailto):/i.test(href)) {
                    link.setAttribute('data-lr-ext', href);
                    return;
                }
                if (isExternal(href)) return;
                const [pathPart, anchor = ''] = href.split('#');
                const path = pathPart ? resolvePath(dir, pathPart) : item.path;
                const target = indexOfPath.get(path.toLowerCase());
                if (target !== undefined) link.setAttribute('data-lr-href', `${target}#${decodeAnchor(anchor)}`);
            });

            markTypography(fragment);

            fragment.querySelectorAll('[style]').forEach((node) => {
                const style = cleanInlineStyle(node.getAttribute('style'));
                if (style) node.setAttribute('style', style); else node.removeAttribute('style');
            });

            // Последний рубеж: любой адрес — только blob: книги или #.
            fragment.querySelectorAll('*').forEach((node) => {
                URL_ATTRS.forEach((name) => {
                    const value = name === 'xlink:href' ? node.getAttributeNS(XLINK, 'href') : node.getAttribute(name);
                    if (value === null || value === undefined) return;
                    if (/^blob:/i.test(value) || value.startsWith('#')) return;
                    if (name === 'xlink:href') node.removeAttributeNS(XLINK, 'href'); else node.removeAttribute(name);
                });
            });

            const lang = doc.documentElement.getAttribute('xml:lang') || doc.documentElement.getAttribute('lang') || '';
            return { fragment, css: cssParts.join('\n'), lang };
        })();
        cache.set(index, promise);
        return promise;
    };

    const destroy = () => {
        destroyed = true;
        cache.clear();
        blobUrls.forEach((promise) => { promise.then((url) => { if (url) URL.revokeObjectURL(url); }); });
        blobUrls.clear();
    };

    return { spine, loadSection, indexOfPath: (path) => indexOfPath.get(String(path).toLowerCase()), destroy };
}
