/* Общее для пакетов Office: zip, связи частей (rels) и картинки. */
import JSZip from 'jszip';
import { attr, childrenOf, parseXml } from './xmlTree.js';

/* Понятная человеку причина, почему файл не открылся. Окно узнаёт её по имени,
   а не по классу: класс живёт в модуле с jszip, и тянуть его в окно ради
   instanceof незачем. */
export class PreviewError extends Error {
    constructor(message) {
        super(message);
        this.name = 'PreviewError';
    }
}

export async function openPackage(bytes) {
    try {
        return await JSZip.loadAsync(bytes);
    } catch {
        throw new PreviewError('Файл повреждён или это не документ Office.');
    }
}

export async function readText(zip, path) {
    const file = path ? zip.file(path) : null;
    return file ? file.async('string') : null;
}

export async function readXml(zip, path) {
    const text = await readText(zip, path);
    return text === null ? null : parseXml(text);
}

/** Путь цели связи относительно части-источника: 'media/a.png' от 'word/document.xml'. */
export function resolvePartPath(base, target) {
    const clean = String(target || '').replace(/\\/g, '/');
    if (clean.startsWith('/')) return clean.slice(1);
    const parts = String(base || '').split('/');
    parts.pop();
    clean.split('/').forEach((segment) => {
        if (segment === '..') parts.pop();
        else if (segment && segment !== '.') parts.push(segment);
    });
    return parts.join('/');
}

const relsPathFor = (partPath) => {
    const at = partPath.lastIndexOf('/');
    return `${partPath.slice(0, at + 1)}_rels/${partPath.slice(at + 1)}.rels`;
};

/** Связи части: Map(id → { target, type, external }). */
export async function readRels(zip, partPath) {
    const map = new Map();
    const xml = await readXml(zip, relsPathFor(partPath));
    const root = xml ? xml.children.find((node) => typeof node === 'object') : null;
    childrenOf(root, 'Relationship').forEach((rel) => {
        const external = String(attr(rel, 'TargetMode') || '').toLowerCase() === 'external';
        const target = attr(rel, 'Target') || '';
        map.set(attr(rel, 'Id'), {
            type: attr(rel, 'Type') || '',
            external,
            target: external ? target : resolvePartPath(partPath, target),
        });
    });
    return map;
}

/** Первая связь заданного типа (окончание адреса типа: '/styles', '/numbering'). */
export function relOfType(rels, suffix) {
    for (const rel of rels.values()) {
        if (rel.type.endsWith(suffix)) return rel;
    }
    return null;
}

/** Главная часть пакета по _rels/.rels — у генераторов не от Microsoft она бывает не на привычном месте. */
export async function mainPartPath(zip, fallback) {
    const rels = await readRels(zip, '');
    const main = relOfType(rels, '/officeDocument');
    return main && zip.file(main.target) ? main.target : fallback;
}

// Что умеет показать браузер. EMF/WMF (вставки из Office) — нет.
const IMAGE_TYPES = {
    png: 'image/png',
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    jfif: 'image/jpeg',
    gif: 'image/gif',
    bmp: 'image/bmp',
    webp: 'image/webp',
    svg: 'image/svg+xml',
};

export const imageTypeOf = (path) => {
    const match = /\.([A-Za-z0-9]+)$/.exec(String(path || ''));
    return match ? IMAGE_TYPES[match[1].toLowerCase()] || null : null;
};

/** Байты картинок пакета: Map(путь → { type, bytes }). Неизвестные браузеру форматы пропускаются. */
export async function loadImages(zip, paths, maxBytes = 40 * 1024 * 1024) {
    const out = new Map();
    let total = 0;
    for (const path of paths) {
        const type = imageTypeOf(path);
        const file = type ? zip.file(path) : null;
        if (!file) continue;
        // eslint-disable-next-line no-await-in-loop
        const bytes = await file.async('uint8array');
        total += bytes.length;
        if (total > maxBytes) break;
        out.set(path, { type, bytes });
    }
    return out;
}
