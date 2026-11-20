/* Разбор уже собранного меню сайдбара на строки — для личной раскладки.
 *
 * ПОЧЕМУ ПО ЭЛЕМЕНТАМ REACT, А НЕ ПО ВЕТКАМ App.jsx. Меню — дерево JSX на две
 * тысячи строк, ветвящееся по ролям, и около тридцати тестов сверяют его как
 * ТЕКСТ (число вхождений, порядок строк). Дописать каждому из ~120 пунктов
 * свой признак значило бы сломать их все. Готовое дерево — это уже то, что
 * человек увидит: гейты доступа отработали, фильтр отдела тоже; остаётся
 * узнать каждую строку и переставить. Узнаётся строка по подписи
 * (`.sidebar-text`) — она одна у раздела в меню человека.
 *
 * Без JSX и без DOM: проверяется node --test. */
import React from 'react';
import { cleanText, KEY_MAX } from './sidebarLayoutModel.js';

const hasClass = (className, name) => (
    typeof className === 'string' && className.split(/\s+/).includes(name)
);

/** Строки меню по порядку: { kind: 'item' | 'hr' | 'raw', element }.
 *  transparent — компоненты-обёртки без своей разметки (SidebarDeptScope):
 *  их зовём как функцию и разбираем то, что они вернули. Незнакомый
 *  компонент остаётся строкой «raw» на своём месте — меню не теряет его. */
export const flattenMenu = (children, { transparent = [] } = {}) => {
    const out = [];
    const visit = (node) => {
        if (node == null || typeof node === 'boolean') return;
        if (Array.isArray(node)) {
            node.forEach(visit);
            return;
        }
        if (!React.isValidElement(node)) return;
        const { type } = node;
        if (type === React.Fragment) {
            visit(node.props.children);
            return;
        }
        if (transparent.includes(type)) {
            visit(type(node.props));
            return;
        }
        if (type === 'li') out.push({ kind: 'item', element: node });
        else if (type === 'hr') out.push({ kind: 'hr', element: node });
        else out.push({ kind: 'raw', element: node });
    };
    visit(children);
    return out;
};

/* Первая непустая строка внутри `.sidebar-text`. Счётчики стоят ПОСЛЕ
   подписи (и в уголке плитки — вне `.sidebar-text`), так что первая строка —
   всегда сама подпись: «Задачи», а не «Задачи3». */
const findLabel = (node, inText) => {
    if (node == null || typeof node === 'boolean') return null;
    if (typeof node === 'string' || typeof node === 'number') {
        const text = String(node).trim();
        return inText && text ? text : null;
    }
    if (Array.isArray(node)) {
        for (const child of node) {
            const found = findLabel(child, inText);
            if (found) return found;
        }
        return null;
    }
    if (!React.isValidElement(node)) return null;
    const host = typeof node.type === 'string';
    const here = inText || (host && hasClass(node.props.className, 'sidebar-text'));
    return findLabel(node.props.children, here);
};

/** Подпись строки меню — она же ключ раздела в раскладке; null, если подписи нет. */
export const itemLabelOf = (element) => {
    const label = findLabel(element?.props?.children, false);
    return label ? cleanText(label, KEY_MAX) : null;
};

const walkHosts = (node, visit) => {
    if (node == null || typeof node === 'boolean' || typeof node !== 'object') return;
    if (Array.isArray(node)) {
        node.forEach((child) => walkHosts(child, visit));
        return;
    }
    if (!React.isValidElement(node)) return;
    if (typeof node.type === 'string') visit(node);
    walkHosts(node.props.children, visit);
};

const textOf = (node) => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textOf).join('');
    return React.isValidElement(node) ? textOf(node.props.children) : '';
};

/* Цвет счётчика — по классу из разметки пункта: задачи и ивенты — rose,
   чаты — orange, опросы — amber, «Обращения» и «Жалобы» — белая плашка. */
const BADGE_TONES = [
    ['rose', 'bg-rose-500'],
    ['orange', 'bg-orange-500'],
    ['amber', 'bg-amber-500'],
    ['white', 'bg-white/90'],
];

/** Счётчик строки: { count, plus, tone } или null. Счётчик — круглая
 *  плашка с числом; у пункта их бывает две (уголок плитки в рельсе — до «9+»,
 *  и в подписи — до «99+»), берётся наибольшее, то есть точное, число. */
export const itemBadgeOf = (element) => {
    let best = null;
    walkHosts(element?.props?.children, (node) => {
        const { className } = node.props;
        if (!hasClass(className, 'rounded-full')) return;
        const text = textOf(node.props.children).trim();
        if (!/^\d+\+?$/.test(text)) return;
        const count = Number.parseInt(text, 10);
        if (!(count > 0)) return;
        const tone = BADGE_TONES.find(([, name]) => hasClass(className, name))?.[0] || 'rose';
        if (!best || count > best.count) best = { count, plus: text.endsWith('+'), tone };
    });
    return best;
};

/** Значок строки меню — первый компонент (не тег) внутри неё: в пунктах App.jsx
 *  это FaIcon, и стоит он раньше подписи и счётчиков. Нужен панели разделов и
 *  «парящей» строке, чтобы раздел узнавался там так же, как в сайдбаре. */
export const itemIconOf = (element) => {
    let icon = null;
    const visit = (node) => {
        if (icon || node == null || typeof node !== 'object') return;
        if (Array.isArray(node)) {
            node.forEach(visit);
            return;
        }
        if (!React.isValidElement(node)) return;
        if (typeof node.type === 'string' || node.type === React.Fragment) {
            visit(node.props.children);
            return;
        }
        icon = node;
    };
    visit(element?.props?.children);
    return icon;
};

/** Открыт ли сейчас раздел этой строки: его кнопку разметка красит bg-blue-700. */
export const itemIsActive = (element) => {
    let active = false;
    walkHosts(element?.props?.children, (node) => {
        if (node.type === 'button' && (hasClass(node.props.className, 'bg-blue-700') || hasClass(node.props.className, 'bg-blue-800'))) {
            active = true;
        }
    });
    return active;
};

/** Меню целиком: строки с ключами и стандартные карточки.
 *  Строка без подписи и незнакомый компонент получают ключ по месту ('#…'):
 *  они рисуются там же, где стояли, но в раскладку не сохраняются. */
export const readMenu = (children, options) => {
    const flat = flattenMenu(children, options);
    const items = new Map();
    const defaultBlocks = [];
    let block = [];
    let divider = null;
    flat.forEach((entry, index) => {
        if (entry.kind === 'hr') {
            divider = divider || entry.element;
            if (block.length) defaultBlocks.push(block);
            block = [];
            return;
        }
        const label = entry.kind === 'item' ? itemLabelOf(entry.element) : null;
        let key = label || `#${entry.kind}:${index}`;
        for (let n = 2; items.has(key); n += 1) key = `${label || `#${entry.kind}:${index}`} #${n}`;
        items.set(key, { key, element: entry.element, fixed: !label });
        block.push(key);
    });
    if (block.length) defaultBlocks.push(block);
    return { items, defaultBlocks, divider };
};
