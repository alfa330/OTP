// Окно поверх других окон: перенос стилей и темы в чужой документ.
//
// Проверять тут стоит ровно одно место, и оно же — причина, по которой модуль
// вообще появился. Две прежние копии этого кода разъехались на одной строке:
// табло СЗоВ подставляло клонированной <link> РАЗРЕШЁННЫЙ href, а закреплённая
// задача копировала атрибут как есть. У PiP-окна свой базовый адрес, поэтому
// относительный путь в нём ищется от другого корня, и окно открывается голым
// HTML — без единой ошибки в консоли и без падения сборки.
//
// Запуск: node --test tests/pip_window.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';

import { cloneDocumentStyles, mirrorDocumentChrome, mirrorDocumentStyles } from '../src/utils/pipWindow.js';

/** Узел <link> так, как его отдаёт браузер: атрибут относительный, свойство — нет. */
const linkNode = (attrHref, absoluteHref) => {
    const node = {
        tagName: 'LINK',
        rel: 'stylesheet',
        href: absoluteHref,
        attrHref,
        cloneNode() { return { tagName: 'LINK', rel: 'stylesheet', href: this.attrHref }; },
    };
    return node;
};

// Неглубокий клон в браузере — <style> без текста: стили уехали бы в окно пустыми.
const styleNode = (text) => ({
    tagName: 'STYLE',
    textContent: text,
    cloneNode(deep) { return { tagName: 'STYLE', textContent: deep ? text : '' }; },
});

/** Селектор разбираем сами и только в двух формах, которыми в окно берут стили. */
const matches = (node, selector) => selector.split(',').map((part) => part.trim()).some((part) => (
    (part === 'style' && node.tagName === 'STYLE')
    || (part === 'link[rel="stylesheet"]' && node.tagName === 'LINK' && node.rel === 'stylesheet')
));

const fakeSource = (nodes, { theme = null, bodyClass = '' } = {}) => ({
    querySelectorAll: (selector) => nodes.filter((node) => matches(node, selector)),
    documentElement: { getAttribute: (name) => (name === 'data-otp-theme' ? theme : null) },
    body: { className: bodyClass },
});

const fakeTarget = () => {
    const head = [];
    const attrs = {};
    return {
        head,
        attrs,
        window: {
            document: {
                head: { appendChild: (node) => head.push(node) },
                documentElement: { setAttribute: (name, value) => { attrs[name] = value; } },
                body: { className: '' },
            },
        },
    };
};

const withDocument = (source, run) => {
    const previous = globalThis.document;
    globalThis.document = source;
    try {
        return run();
    } finally {
        if (previous === undefined) delete globalThis.document;
        else globalThis.document = previous;
    }
};

test('ссылка на стили едет в окно с РАЗРЕШЁННЫМ адресом, а не с относительным', () => {
    const source = fakeSource([linkNode('/assets/main-abc.css', 'https://portal.example/assets/main-abc.css')]);
    const target = fakeTarget();
    withDocument(source, () => cloneDocumentStyles(target.window));

    assert.equal(target.head.length, 1);
    // Это и есть строка, на которой копии разъехались: без неё в окне остался бы
    // «/assets/main-abc.css», который PiP-документ ищет от своего корня.
    assert.equal(target.head[0].href, 'https://portal.example/assets/main-abc.css');
});

test('встроенные стили переносятся вместе со ссылками', () => {
    const source = fakeSource([
        styleNode('.a{color:red}'),
        linkNode('/x.css', 'https://portal.example/x.css'),
    ]);
    const target = fakeTarget();
    withDocument(source, () => cloneDocumentStyles(target.window));

    assert.deepEqual(target.head.map((n) => n.tagName), ['STYLE', 'LINK']);
    assert.equal(target.head[0].textContent, '.a{color:red}');
});

/** Узел, который можно вставить в середину <head> и снять: этого требует слежение. */
const liveNode = (tagName, { attrHref = '', href = '', textContent = '' } = {}) => ({
    tagName,
    rel: tagName === 'LINK' ? 'stylesheet' : undefined,
    href,
    attrHref,
    textContent,
    parentNode: null,
    get nextSibling() {
        const siblings = this.parentNode?.childNodes || [];
        return siblings[siblings.indexOf(this) + 1] ?? null;
    },
    remove() {
        if (!this.parentNode) return;
        this.parentNode.childNodes.splice(this.parentNode.childNodes.indexOf(this), 1);
        this.parentNode = null;
    },
    // Как и у linkNode выше: клон получает атрибут, то есть относительный адрес.
    cloneNode(deep) { return liveNode(tagName, { attrHref, href: attrHref, textContent: deep ? this.textContent : '' }); },
    isEqualNode(other) {
        return this.tagName === other.tagName && this.href === other.href
            && this.textContent === other.textContent;
    },
});

const liveHead = () => {
    const head = {
        childNodes: [],
        // Каждая постановка узла: по ней видно, не трогали ли то, что не менялось.
        placed: [],
        appendChild(node) { node.remove(); node.parentNode = head; head.childNodes.push(node); head.placed.push(node); },
        insertBefore(node, next) {
            node.remove();
            node.parentNode = head;
            head.childNodes.splice(head.childNodes.indexOf(next), 0, node);
            head.placed.push(node);
        },
    };
    return head;
};

test('ссылка, догруженная после открытия окна, тоже едет с разрешённым адресом', () => {
    const styles = [liveNode('LINK', { attrHref: '/assets/main-abc.css', href: 'https://portal.example/assets/main-abc.css' })];
    const head = liveHead();
    const targetWindow = {
        closed: false,
        document: { head, createComment: (text) => liveNode(null, { textContent: text }) },
    };
    const observers = [];
    const previousObserver = globalThis.MutationObserver;
    globalThis.MutationObserver = class {
        constructor(callback) { this.callback = callback; observers.push(this); }
        observe(node, options) { this.node = node; this.options = options; }
        disconnect() { this.disconnected = true; }
    };
    const source = { head: {}, querySelectorAll: (selector) => styles.filter((node) => matches(node, selector)) };
    try {
        withDocument(source, () => {
            const stop = mirrorDocumentStyles(targetWindow);
            const [opened] = head.childNodes;
            assert.equal(opened.href, 'https://portal.example/assets/main-abc.css');
            // Следим за <head> портала целиком: Vite переписывает текст <style> на месте,
            // и без subtree с characterData окно такой правки не увидит.
            assert.equal(observers[0].node, source.head);
            assert.deepEqual(observers[0].options, {
                childList: true, subtree: true, characterData: true, attributes: true,
            });

            // Таблица стилей PDF приходит с первым PDF — окно к этому времени открыто.
            styles.push(liveNode('LINK', { attrHref: '/assets/pdf-lazy.css', href: 'https://portal.example/assets/pdf-lazy.css' }));
            observers[0].callback();

            assert.deepEqual(head.childNodes.filter((node) => node.tagName).map((node) => node.href), [
                'https://portal.example/assets/main-abc.css',
                'https://portal.example/assets/pdf-lazy.css',
            ]);
            // Переставленный <link> браузер загружает заново — окно мигнуло бы без стилей.
            assert.equal(head.placed.filter((node) => node === opened).length, 1,
                'ссылку, которая не менялась, с места не снимают');

            stop();
            assert.deepEqual(head.childNodes, [], 'после остановки в окне не остаётся ни клонов, ни метки');
            assert.equal(observers[0].disconnected, true);
        });
    } finally {
        if (previousObserver === undefined) delete globalThis.MutationObserver;
        else globalThis.MutationObserver = previousObserver;
    }
});

test('тёмный режим уезжает в окно: он включается атрибутом, а не темой системы', () => {
    const source = fakeSource([], { theme: 'dark', bodyClass: 'bg-gray-50 font-sans' });
    const target = fakeTarget();
    withDocument(source, () => mirrorDocumentChrome(target.window));

    assert.equal(target.attrs['data-otp-theme'], 'dark');
    assert.equal(target.window.document.body.className, 'bg-gray-50 font-sans');
});

test('светлая тема не проставляет атрибут — иначе слой темы включится наполовину', () => {
    const source = fakeSource([], { theme: null, bodyClass: 'bg-gray-50' });
    const target = fakeTarget();
    withDocument(source, () => mirrorDocumentChrome(target.window));

    assert.equal(target.attrs['data-otp-theme'], undefined);
});
